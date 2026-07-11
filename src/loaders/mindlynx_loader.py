"""
三系统输出数据加载适配器（零侵入版）

核心原则：不修改三个原有系统的任何代码。
所有读取逻辑完全自包含在 fusion_system 内部。

数据获取方式：
- lynx_vnpy:     通过 Python import 直接调用 lynx_signal.py 的导出函数
                  优先读取统一缓存 (UnifiedCache)，缓存未命中时回退到 Sina API
- MindLynx:      读取 reports/ 目录下已生成的 Markdown 报告文件
- TradingAgent:  读取 ~/.mind_tradingagent/logs/ 目录下已输出的 JSON 日志

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.unified_cache import UnifiedCache, get_cache

logger = logging.getLogger(__name__)

class MindLynxDataLoader:
    """
    MindLynx 信号读取器（零侵入版）

    不修改 MindLynx-Aistock 的任何代码。
    读取 reports/ 目录下已存在的分析报告 Markdown 文件。

    报告格式（来自 daily analysis）:
        🟡 **皖新传媒(601801)**: 持有 | 评分 52 | 震荡偏多
        ⚪ **古麒绒材(001390)**: 观望 | 评分 46 | 看空
        🔴 ***ST网达(603189)**: 卖出 | 评分 34 | 强烈看空

    使用方式:
        loader = MindLynxDataLoader()
        signals = loader.load_by_date("2026-05-29")
        # 返回: {"601801": {"signal": "持有", "score": 52, "trend": "震荡偏多"}, ...}
    """

    def __init__(self, reports_dir: str = "systems/MindLynx-Aistock/reports/"):
        self.reports_dir = Path(reports_dir).expanduser().resolve()

    def load_by_date(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """从 Markdown 报告解析信号，优先从 ML 的 analysis_history DB 获取结构化数据"""
        # 优先从 ML SQLite DB 获取结构化分析数据
        db_signals = self._load_from_db(date_str)
        if db_signals:
            logger.info(f"MindLynx: 从 analysis_history DB 获取 {len(db_signals)} 只股票结构化数据")
            return db_signals

        # 后备: 解析 Markdown 报告
        signals = self._parse_report(date_str)
        if signals:
            return signals

        logger.warning(f"MindLynx 报告未找到 ({date_str})")
        return {}

    # ── ML stock_analysis.db 结构化数据读取 ──────────────────────

    def _load_from_db(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """从 ML 的 stock_analysis.db → analysis_history 表读取结构化分析结果."""
        db_path = Path("systems/MindLynx-Aistock/data/stock_analysis.db").resolve()
        if not db_path.exists():
            return {}

        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT ah.code, ah.name, ah.sentiment_score, ah.trend_prediction,
                       ah.operation_advice, ah.analysis_summary, ah.raw_result,
                       ah.context_snapshot, ah.ideal_buy, ah.stop_loss, ah.take_profit
                FROM analysis_history ah
                INNER JOIN (
                    SELECT code, MAX(created_at) as max_created
                    FROM analysis_history
                    WHERE date(created_at) = ?
                      AND code IS NOT NULL AND code != '' AND code != 'MARKET'
                    GROUP BY code
                ) latest ON ah.code = latest.code AND ah.created_at = latest.max_created
                ORDER BY ah.code
            """, (date_str,)).fetchall()
            conn.close()

            import json
            signals = {}
            for row in rows:
                code = row["code"]
                raw = row["raw_result"] or ""
                ctx = row["context_snapshot"] or ""
                try:
                    raw = row["raw_result"] or "{}"
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    dashboard = parsed.get("dashboard", {}) if isinstance(parsed, dict) else {}

                    # Extract dashboard numeric fields
                    dp = dashboard.get("data_perspective", {})
                    ts = dp.get("trend_status", {})
                    pp = dp.get("price_position", {})
                    va = dp.get("volume_analysis", {})
                    intel = dashboard.get("intelligence", {})
                    battle = dashboard.get("battle_plan", {})
                    sniper = battle.get("sniper_points", {}) if battle else {}

                    # Compute factor_baseline from context_snapshot factor_zscores
                    factor_baseline: float | None = None
                    try:
                        ctx_parsed = json.loads(ctx) if isinstance(ctx, str) else {}
                        if isinstance(ctx_parsed, dict):
                            fz = ctx_parsed.get("factor_zscores", {}) or {}
                            stock_z = fz.get(code, {}) if isinstance(fz, dict) else {}
                            if stock_z and isinstance(stock_z, dict):
                                z_vals = [v for v in stock_z.values() if isinstance(v, (int, float))]
                                if z_vals:
                                    avg_z = sum(z_vals) / len(z_vals)
                                    # Map z-score [-3,+3] to [0,100]
                                    factor_baseline = round(50.0 + avg_z * 50.0 / 3.0, 1)
                    except Exception:
                        logger.debug("[MindLynx] factor_baseline 计算失败(code=%s): 可能是factor_zscores缺失或格式异常", code)

                    signals[code] = {
                        "code": code,
                        "name": row["name"] or "",
                        "signal": row["operation_advice"] or "观望",
                        "score": row["sentiment_score"] or 50,
                        "trend": row["trend_prediction"] or "",
                        "sentiment_score": row["sentiment_score"],
                        "trend_prediction": row["trend_prediction"] or "",
                        "operation_advice": row["operation_advice"] or "观望",
                        "analysis_summary": row["analysis_summary"] or "",
                        "ideal_buy": row["ideal_buy"],
                        "stop_loss": row["stop_loss"],
                        "take_profit": row["take_profit"],
                        # Factor baseline for hallucination detection
                        "factor_baseline": factor_baseline,
                        # Dashboard-extracted fields
                        "ml_trend_score": ts.get("trend_score"),
                        "ml_support_level": pp.get("support_level"),
                        "ml_resistance_level": pp.get("resistance_level"),
                        "ml_volume_ratio_dash": va.get("volume_ratio"),
                        "ml_turnover_rate": va.get("turnover_rate"),
                        "ml_risk_alert_count": len(intel.get("risk_alerts", [])),
                        "ml_catalyst_count": len(intel.get("positive_catalysts", [])),
                        "source": "analysis_db",
                    }
                except (json.JSONDecodeError, AttributeError, TypeError):
                    # Fallback: simple fields without dashboard
                    signals[code] = {
                        "code": code,
                        "name": row["name"] or "",
                        "signal": row["operation_advice"] or "观望",
                        "score": row["sentiment_score"] or 50,
                        "trend": row["trend_prediction"] or "",
                        "sentiment_score": row["sentiment_score"],
                        "trend_prediction": row["trend_prediction"] or "",
                        "operation_advice": row["operation_advice"] or "观望",
                        "analysis_summary": row["analysis_summary"] or "",
                        "ideal_buy": row["ideal_buy"],
                        "stop_loss": row["stop_loss"],
                        "take_profit": row["take_profit"],
                        "source": "analysis_db",
                    }
            return signals
        except Exception as e:
            logger.debug(f"MindLynx DB 查询失败: {e}")
            return {}

    def _parse_report(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """
        解析 Markdown 报告中的信号行。

        支持两种文件名格式:
          - reports/report_2026-05-29.md
          - reports/report_20260529.md
        """
        patterns = [
            self.reports_dir / f"report_{date_str}.md",
            self.reports_dir / f"report_{date_str.replace('-', '')}.md",
        ]

        for report_path in patterns:
            if not report_path.exists():
                continue

            try:
                content = report_path.read_text(encoding="utf-8")
            except IOError as e:
                logger.warning(f"读取报告失败 {report_path}: {e}")
                continue

            signals = self._extract_signals_from_markdown(content)
            if signals:
                logger.info(f"MindLynx: 从 {report_path.name} 解析到 {len(signals)} 只股票")
                return signals

        return {}

    @staticmethod
    def _extract_signals_from_markdown(content: str) -> Dict[str, Dict[str, Any]]:
        """
        从 Markdown 文本中提取信号。

        匹配格式:
          EMOJI **NAME(CODE)**: SIGNAL | 评分 SCORE | TREND

        其中 EMOJI 可以是 🟢🟡🔴⚪ 中的任何一个，
        SCORE 可以是 0-99 之间的整数。
        """
        signals = {}

        # 添加对 *ST 等特殊股票名的处理 — 连续 ** 可能被截断
        # 宽松匹配: 支持 **NAME(CODE)**: 和 **NAME(CODE)** : 两种格式
        # 以及 *ST 等含星号前缀的股票名
        # 报告格式已扩展为包含股价数据: ¥5.83 +2.6% | SIGNAL | 评分 SCORE
        pattern = re.compile(
            r"^[🟢🟡🔴⚪🟠]\s+\*{0,2}(.+?)\((\w+)\)\*{0,2}\s*:\s*(?:[^|]*\|\s*)?(\S+)\s*\|\s*评分\s*(\d+)",
            re.MULTILINE,
        )

        for match in pattern.finditer(content):
            raw_name = match.group(1)
            code = match.group(2)
            signal = match.group(3)
            score = int(match.group(4))

            # 清理名称（去掉多余的尾部 *）
            name = raw_name.rstrip("*").strip()

            # 提取趋势预测
            line_end = content.find("\n", match.end())
            trend = ""
            if line_end > 0:
                rest = content[match.end():line_end].strip()
                if "|" in rest:
                    trend = rest.split("|")[-1].strip()

            signals[code] = {
                "code": code,
                "name": name,
                "signal": signal,
                "score": score,
                "trend": trend,
                "source": "report_markdown",
            }

        return signals

    def load_market_review(self, date_str: str, session: str = "全天") -> Optional[str]:
        """读取大盘复盘报告内容"""
        patterns = [
            self.reports_dir / f"market_review_{date_str}_{session}.md",
            self.reports_dir / f"market_review_{date_str}.md",
            self.reports_dir / f"market_review_{date_str.replace('-', '')}_{session}.md",
        ]
        for path in patterns:
            if path.exists():
                try:
                    return path.read_text(encoding="utf-8")
                except IOError:
                    logger.warning(f"读取市场复盘报告失败: {path}")
        return None

    def get_latest_available_date(self) -> Optional[str]:
        """获取最新可用报告的日期（按实际日期排序，非文件名）"""
        dates = []
        for f in self.reports_dir.glob("report_*.md"):
            match = re.search(r"report_(\d{8}|\d{4}-\d{2}-\d{2})\.md", f.name)
            if match:
                raw = match.group(1)
                if len(raw) == 8:
                    raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
                dates.append(raw)
        if not dates:
            return None
        return max(dates)
