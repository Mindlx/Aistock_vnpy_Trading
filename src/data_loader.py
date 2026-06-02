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


# ══════════════════════════════════════════════
# 零侵入方案: lynx_vnpy — Python import 直接调用
# ══════════════════════════════════════════════

class LynxDataLoader:
    """
    lynx_vnpy 信号读取器（零侵入版）

    不修改 lynx_signal.py 的任何代码。
    通过 Python import 机制直接调用其导出函数:
      - fetch_daily_bars(code) → DataFrame
      - compute_features(df) → DataFrame  
      - predict_signal(df, code, name) → dict

    模型文件位置: lynx_vnpy/models/{code}_model.pkl
    模型会自动按需训练或加载（lynx_signal.py 内部处理）。

    统一缓存集成（v2.0）:
      - 优先从 UnifiedCache (SQLite) 读取 OHLCV 数据
      - 缓存命中 → 跳过 Sina API 调用 (~60% API 调用减少)
      - 缓存未命中 → 调用 fetch_daily_bars → 自动写入缓存
      - 缓存默认 TTL: 24h (daily_ohlcv)，可通过 settings.yaml 调整
      - 零侵入: 不修改 lynx_signal.py 任何代码

    使用方式:
        loader = LynxDataLoader()
        signals = loader.load_by_date("2026-05-29")
        # 返回: {"601801": {"signal": "🟢 买入", "prob_up": 72.0, ...}, ...}
    """

    def __init__(
        self,
        lynx_project_root: str = "systems/lynx_vnpy",
        cache: Optional[UnifiedCache] = None,
        cache_enabled: bool = True,
        cache_ttl: int = 86400,
    ):
        """
        参数:
            lynx_project_root: lynx_vnpy 项目根目录的相对路径
            cache: UnifiedCache 实例。None 时使用全局默认缓存。
            cache_enabled: 是否启用缓存。设为 False 则每次强制从 API 获取。
            cache_ttl: 缓存 TTL（秒），默认 86400 (1天)。
        """
        self.lynx_root = Path(lynx_project_root).expanduser().resolve()
        self._imported = False
        self._lynx_module = None
        self._cache_enabled = cache_enabled
        self._cache_ttl = cache_ttl
        self._cache = cache if cache is not None else get_cache()
        self._cache_stats = {"hits": 0, "misses": 0, "fresh": 0}

    def _ensure_imported(self):
        """确保 lynx_signal 模块已导入（只导入一次）"""
        if self._imported:
            return True

        lynx_signal_path = self.lynx_root / "lynx_signal.py"
        if not lynx_signal_path.exists():
            logger.error(f"lynx_signal.py 不存在: {lynx_signal_path}")
            return False

        # 将 lynx_vnpy 项目根加入 sys.path
        if str(self.lynx_root) not in sys.path:
            sys.path.insert(0, str(self.lynx_root))

        try:
            import lynx_signal
            self._lynx_module = lynx_signal
            self._imported = True
            logger.info(f"lynx_signal 已导入 (from {lynx_signal_path})")
            return True
        except ImportError as e:
            logger.error(f"导入 lynx_signal 失败: {e}")
            # 可能缺少依赖，打印更详细的错误
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def get_stock_list(self) -> List[str]:
        """获取 lynx_vnpy 的股票列表"""
        if not self._ensure_imported():
            return []
        return list(self._lynx_module.STOCK_CODES)

    def _fetch_with_cache(self, code: str) -> Optional[Any]:
        """Fetch OHLCV data with cache-first strategy.

        Returns DataFrame in Sina Chinese-column format (compatible with
        compute_features), or None if data unavailable.

        Flow:
            1. Check UnifiedCache (SQLite) → if hit & fresh, return remapped data
            2. Call lynx.fetch_daily_bars(code) → Sina API
            3. Store result in UnifiedCache → return
        """
        if self._cache_enabled:
            # Check if already fresh (skip API entirely if data is recent)
            if self._cache.is_fresh(code, "daily_ohlcv", self._cache_ttl):
                df_cached = self._cache.get_daily_ohlcv(
                    code, days=120, ttl_seconds=self._cache_ttl,
                    reverse_map=True, reverse_map_source="sina",
                )
                if df_cached is not None and not df_cached.empty:
                    self._cache_stats["hits"] += 1
                    self._cache_stats["fresh"] += 1
                    logger.debug(f"[LynxCache] {code} → fresh cache hit ({len(df_cached)} rows)")
                    return df_cached

            # Try cache even if not marked fresh (stale data better than API failure)
            df_cached = self._cache.get_daily_ohlcv(
                code, days=120, reverse_map=True, reverse_map_source="sina",
            )
            if df_cached is not None and not df_cached.empty:
                self._cache_stats["hits"] += 1
                logger.debug(f"[LynxCache] {code} → cache hit ({len(df_cached)} rows)")
                return df_cached

        # Cache miss: fetch from Sina API
        self._cache_stats["misses"] += 1
        lynx = self._lynx_module
        df = lynx.fetch_daily_bars(code)

        # Store to cache on success
        if df is not None and not df.empty and self._cache_enabled:
            try:
                self._cache.put_daily_ohlcv(code, df, source="sina")
                logger.debug(f"[LynxCache] {code} → stored to cache ({len(df)} rows)")
            except Exception as e:
                logger.warning(f"[LynxCache] {code} store failed: {e}")

        return df

    @property
    def cache_stats(self) -> Dict[str, int]:
        """Return cache hit/miss statistics for monitoring."""
        return dict(self._cache_stats)

    def load_by_date(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """
        通过直接调用 lynx_signal.py 的导出函数生成信号。

        不调用 run()（会打印+推送），而是逐只股票调用：
          _fetch_with_cache (cache-first) → compute_features → predict_signal

        返回:
            {stock_code: signal_dict, ...}
            signal_dict 包含: signal, strength, prob_up, rsi, macd_hist, atr_ratio, ...
        """
        if not self._ensure_imported():
            logger.warning("lynx_signal 无法导入，返回空信号")
            return {}

        lynx = self._lynx_module
        stock_codes = self.get_stock_list()
        if not stock_codes:
            return {}

        logger.info(
            f"lynx_vnpy: 开始获取 {len(stock_codes)} 只股票信号 "
            f"(缓存: {'启用' if self._cache_enabled else '禁用'})..."
        )
        results = {}

        for code in stock_codes:
            try:
                # 1. 获取日K数据（缓存优先 → Sina API 回退）
                df = self._fetch_with_cache(code)
                if df is None:
                    logger.warning(f"lynx_vnpy [{code}]: 无数据")
                    time.sleep(2)
                    continue

                # 2. 计算特征
                name = df.iloc[-1].get('股票名称', code)
                df_feat = lynx.compute_features(df)

                # 3. 预测信号（模型自动训练/加载）
                sig = lynx.predict_signal(df_feat, code, name)
                if sig:
                    results[code] = sig
                    logger.debug(f"lynx_vnpy [{code}]: {sig['signal']} 置信 {sig['prob_up']}%")
                else:
                    logger.warning(f"lynx_vnpy [{code}]: 信号不足")

                # API 限流保护（缓存命中时无需等待）
                if not self._cache_enabled:
                    time.sleep(2)

            except Exception as e:
                logger.error(f"lynx_vnpy [{code}]: 异常 {e}")
                continue

        h, m = self._cache_stats["hits"], self._cache_stats["misses"]
        saved = h * 2  # ~2s saved per cache hit (API call + sleep)
        logger.info(
            f"lynx_vnpy: 完成，{len(results)}/{len(stock_codes)} 只获得信号 "
            f"(缓存命中 {h}/{h+m}, 节省 ~{saved}s API 调用时间)"
        )
        return results

    def run_original(self) -> List[Dict[str, Any]]:
        """
        直接运行 lynx_signal.run() 的原始逻辑（会打印到控制台）。
        仅用于诊断/调试，融合系统不建议使用。
        """
        if not self._ensure_imported():
            return []
        self._lynx_module.run()
        return []

    def force_cache_refresh(self) -> int:
        """Force-refresh all stock data in cache. Returns count of stocks refreshed."""
        if not self._ensure_imported():
            return 0
        lynx = self._lynx_module
        stock_codes = self.get_stock_list()
        count = 0
        for code in stock_codes:
            df = lynx.fetch_daily_bars(code)
            if df is not None and not df.empty:
                self._cache.put_daily_ohlcv(code, df, source="sina")
                count += 1
                time.sleep(2)
        logger.info(f"[LynxCache] Force-refreshed {count}/{len(stock_codes)} stocks")
        return count


# ══════════════════════════════════════════════
# 零侵入方案: MindLynx — 读取已生成的 Markdown 报告
# ══════════════════════════════════════════════

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
        """从 Markdown 报告解析信号"""
        # 优先解析报告
        signals = self._parse_report(date_str)
        if signals:
            return signals

        # 看看是否有 market review 报告中包含个股信息
        logger.warning(f"MindLynx 报告未找到 ({date_str})")
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
                    pass
        return None

    def get_latest_available_date(self) -> Optional[str]:
        """获取最新可用报告的日期"""
        latest = None
        for f in sorted(self.reports_dir.glob("report_*.md"), reverse=True):
            match = re.search(r"report_(\d{8}|\d{4}-\d{2}-\d{2})\.md", f.name)
            if match:
                date_raw = match.group(1)
                if len(date_raw) == 8:
                    date_raw = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
                latest = date_raw
                break
        return latest


# ══════════════════════════════════════════════
# 零侵入方案: TradingAgent — 读取已输出的 JSON 日志
# ══════════════════════════════════════════════

class TradingAgentDataLoader:
    """
    TradingAgent 信号读取器（零侵入版）

    不修改 mind_TradingAgent 的任何代码。
    读取 ~/.mind_tradingagent/logs/ 下已生成的 JSON 状态日志。

    propagate() 调用后会在以下目录生成日志:
      ~/.mind_tradingagent/logs/{TICKER}/MindTradingAgentStrategy_logs/
        full_states_log_{DATE}.json

    其中 final_trade_decision 字段包含 PortfolioDecision 的 markdown 渲染文本。
    通过解析 "**Rating**: X" 提取 5 级评级。

    使用方式:
        loader = TradingAgentDataLoader()
        result = loader.load_by_stock_and_date("601801", "2026-05-29")
        # 返回: {"rating": "Buy", "price_target": 15.5, "full_decision": "..."}

        # 或扫描所有股票:
        all_results = loader.load_all_by_date("2026-05-29")
    """

    def __init__(self, logs_dir: str = "~/.mind_tradingagent/logs/"):
        self.logs_dir = Path(logs_dir).expanduser().resolve()
        logger.info(f"TradingAgent 日志目录: {self.logs_dir}")

    def load_by_stock_and_date(
        self, stock_code: str, date_str: str
    ) -> Optional[Dict[str, Any]]:
        """
        读取指定股票和日期的决策结果。

        自动尝试多种 ticker 格式：纯代码、.SS、.SZ 后缀。

        参数:
            stock_code: 股票代码（如 "601801"）
            date_str: 日期 YYYY-MM-DD

        返回:
            {"rating": "Buy"/..., "price_target": float|None, "full_decision": str,
             "debate_state": {...}}
            或 None（日志文件不存在）
        """
        ticker = stock_code.upper()
        patterns = self._build_path_patterns(ticker, date_str)

        for log_path in patterns:
            if not log_path.exists():
                continue

            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"TradingAgent 读取失败 {log_path}: {e}")
                continue

            decision = self._extract_decision(state)
            if decision:
                logger.debug(f"TradingAgent [{ticker}]: {decision['rating']}")
                return decision

        return None

    @staticmethod
    def _ticker_variants(ticker: str) -> list[str]:
        """生成 ticker 的多种格式变体"""
        variants = [ticker]
        # 如果输入没有后缀，尝试加 .SS 和 .SZ
        if "." not in ticker:
            variants.append(f"{ticker}.SS")
            variants.append(f"{ticker}.SZ")
        return variants

    def _build_path_patterns(self, ticker: str, date_str: str) -> List[Path]:
        """生成可能的日志文件路径（兼容多种 ticker 格式和日期格式）"""
        date_variants = [
            date_str,                          # 2026-05-29
            date_str.replace("-", ""),         # 20260529
        ]
        patterns = []
        for t in self._ticker_variants(ticker):
            base = self.logs_dir / t / "MindTradingAgentStrategy_logs"
            for d in date_variants:
                patterns.append(base / f"full_states_log_{d}.json")
        return patterns

    @staticmethod
    def _extract_decision(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从状态字典中提取 portfolio manager 的最终决策"""
        final_decision = state.get("final_trade_decision", "")
        if not final_decision:
            # 也可能嵌套在其他 key 下
            for key in ("final_trade_decision", "portfolio_decision"):
                val = state.get(key)
                if val and isinstance(val, str) and "Rating" in val:
                    final_decision = val
                    break

        if not final_decision:
            return None

        # 解析 "**Rating**: Buy" 或 "**Rating** : Buy"
        rating_match = re.search(
            r"\*\*Rating\s*\*\*\s*:\s*(Buy|Overweight|Hold|Underweight|Sell)",
            final_decision,
            re.IGNORECASE,
        )
        rating = rating_match.group(1) if rating_match else "Hold"

        # 提取 Price Target
        price_match = re.search(r"\*\*Price Target\s*\*\*\s*:\s*([\d.]+)", final_decision)
        price_target = float(price_match.group(1)) if price_match else None

        # ═══════════════════════════════════════════════
        # 辩论一致性分析（c1skill Phase 1 — 为幻觉检测准备）
        # ═══════════════════════════════════════════════
        # 从 state 中提取辩论记录，计算投资辩论和风险辩论的一致性分数。
        # 使用关键词计数方法（保持确定性，不引入 LLM 调用）。
        # 结果存入 debate_state 字段，供 reliability.py 使用。
        debate_state = TradingAgentDataLoader._parse_debate_state(state)

        return {
            "rating": rating,
            "price_target": price_target,
            "full_decision": final_decision,
            "debate_state": debate_state,
        }

    @staticmethod
    def _parse_debate_state(state: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析 TradingAgent 辩论状态记录。

        从状态字典中提取投资辩论 (investment_debate_state) 和
        风险辩论 (risk_debate_state) 的文本内容，使用关键词计数
        计算一致性分数。

        返回:
            {
                "investment_agreement": float,  # 0.0 ~ 1.0
                "risk_agreement": float,         # 0.0 ~ 1.0
                "analyst_variance": float,       # 0.0 ~ 1.0（分析师分歧程度）
                "debate_available": bool,         # 辩论数据是否可用
            }
        """
        result = {
            "investment_agreement": 0.5,   # 默认中性
            "risk_agreement": 0.5,
            "analyst_variance": 0.5,
            "debate_available": False,
        }

        # --- 1) 投资辩论: 多头 vs 空头 ---
        inv_debate = state.get("investment_debate_state", "")
        if inv_debate and isinstance(inv_debate, str):
            result["debate_available"] = True
            # 统计多头/空头关键词
            bull_count = sum(1 for w in ["bullish", "看多", "看涨", "买入", "buy", "upside"]
                             if w.lower() in inv_debate.lower())
            bear_count = sum(1 for w in ["bearish", "看空", "看跌", "卖出", "sell", "downside"]
                             if w.lower() in inv_debate.lower())
            total = bull_count + bear_count + 1  # +1 避免除零
            # 一致性 = 1 - 分歧度 = 1 - |bull - bear| / total
            disagreement = abs(bull_count - bear_count) / total
            result["investment_agreement"] = round(1.0 - disagreement, 4)

            # 检查是否有 judge_decision
            if "judge_decision" in inv_debate.lower() or "decision" in inv_debate.lower():
                # 有裁决说明辩论完整，适当提高一致性基线
                result["investment_agreement"] = max(
                    result["investment_agreement"], 0.4
                )

        # --- 2) 风险辩论: 激进 vs 保守 vs 中立 ---
        risk_debate = state.get("risk_debate_state", "")
        if risk_debate and isinstance(risk_debate, str):
            result["debate_available"] = True
            # 统计风险偏好关键词
            agg_count = sum(1 for w in ["激进", "aggressive", "高仓位", "high risk"]
                            if w.lower() in risk_debate.lower())
            cons_count = sum(1 for w in ["保守", "conservative", "低仓位", "low risk"]
                             if w.lower() in risk_debate.lower())
            neut_count = sum(1 for w in ["中立", "neutral", "中等仓位", "medium risk"]
                             if w.lower() in risk_debate.lower())
            total_r = agg_count + cons_count + neut_count + 1
            # 风险一致性: 三个观点越集中越好
            max_view = max(agg_count, cons_count, neut_count)
            result["risk_agreement"] = round(max_view / total_r, 4)

        # --- 3) 分析师分歧: 4 位分析师报告方向一致性 ---
        analyst_reports = []
        for key in state:
            if any(kw in key.lower() for kw in ["analyst", "report", "analysis"]):
                val = state[key]
                if isinstance(val, str):
                    analyst_reports.append(val)

        if analyst_reports:
            result["debate_available"] = True
            # 统计每位分析师的整体情绪
            sentiments = []
            for report in analyst_reports:
                pos = sum(1 for w in ["bullish", "看多", "看涨", "买入", "buy", "positive"]
                          if w.lower() in report.lower())
                neg = sum(1 for w in ["bearish", "看空", "看跌", "卖出", "sell", "negative"]
                          if w.lower() in report.lower())
                if pos > neg:
                    sentiments.append("bullish")
                elif neg > pos:
                    sentiments.append("bearish")
                else:
                    sentiments.append("neutral")

            # 如果分析师们方向一致，variance 低
            unique_sentiments = set(sentiments)
            if len(unique_sentiments) <= 1:
                result["analyst_variance"] = 0.1  # 高度一致
            elif len(unique_sentiments) == 2:
                result["analyst_variance"] = 0.5  # 部分分歧
            else:
                result["analyst_variance"] = 0.9  # 高度分歧（牛熊都有）

        return result

    def load_all_by_date(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """
        扫描 logs/ 下所有股票目录，读取指定日期的决策。

        返回:
            {stock_code: {"rating": str, ...}, ...}
        """
        results: Dict[str, Dict[str, Any]] = {}
        if not self.logs_dir.exists():
            logger.warning(f"TradingAgent 日志目录不存在: {self.logs_dir}")
            return results

        for ticker_dir in sorted(self.logs_dir.iterdir()):
            if not ticker_dir.is_dir() or ticker_dir.name.startswith("."):
                continue
            ticker = ticker_dir.name
            decision = self.load_by_stock_and_date(ticker, date_str)
            if decision:
                results[ticker] = decision

        logger.info(f"TradingAgent: 扫描到 {len(results)} 只股票")
        return results


# ══════════════════════════════════════════════
# 统一加载器: 按股票池组织三系统数据
# ══════════════════════════════════════════════

class UnifiedDataLoader:
    """
    统一数据加载器 — 按股票池组织三个系统的数据。

    每个系统独立加载，互不影响。
    提供 single_source_of_truth 方法：为每只股票生成融合引擎所需的输入。

    使用方式:
        loader = UnifiedDataLoader()
        stock_signals = loader.load_all("2026-05-29")
        # 返回: [{"code": "601801", "name": "皖新传媒",
        #          "lynx_signal": "...", "lynx_prob_up": ...,
        #          "mindlynx_advice": "...", "mindlynx_score": ...,
        #          "tradingagent_rating": "..."}, ...]
    """

    def __init__(
        self,
        lynx_root: str = "systems/lynx_vnpy",
        mindlynx_reports: str = "systems/MindLynx-Aistock/reports/",
        tradingagent_logs: str = "~/.mind_tradingagent/logs/",
        stock_pool_path: str = "config/stock_pool.csv",
    ):
        self.lynx = LynxDataLoader(lynx_root)
        self.mindlynx = MindLynxDataLoader(mindlynx_reports)
        self.tradingagent = TradingAgentDataLoader(tradingagent_logs)
        self.stock_pool = self._load_stock_pool(stock_pool_path)

        logger.info(
            f"UnifiedDataLoader: 股票池 {len(self.stock_pool)} 只, "
            f"系统: lynx/√ mindlynx/√ tradingagent/√"
        )

    @staticmethod
    def _load_stock_pool(path: str) -> List[Dict[str, str]]:
        """从 CSV 加载股票池"""
        import csv
        stocks = []
        csv_path = Path(path)
        if not csv_path.exists():
            logger.warning(f"股票池文件不存在: {path}")
            return []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, skipinitialspace=True)
            for row in reader:
                code = (row.get("code") or "").strip()
                name = (row.get("name") or "").strip()
                if code:
                    stocks.append({"code": code, "name": name})
        return stocks

    def load_all(self, date_str: str) -> List[Dict[str, Any]]:
        """
        加载三系统的所有信号，按股票池组织。

        每个系统独立加载：
        - 如果一个系统暂时不可用（文件不存在/导入失败），自动跳过
        - 融合引擎的 _compute_adjusted_weights 会处理缺失

        返回: fusion_engine.fuse_stock_pool() 可直接接收的格式
        """
        logger.info(f"UnifiedDataLoader: 加载 {date_str} 数据...")

        # 各系统独立加载
        lynx_signals = self.lynx.load_by_date(date_str)
        mindlynx_signals = self.mindlynx.load_by_date(date_str)
        ta_signals = self.tradingagent.load_all_by_date(date_str)

        logger.info(
            f"数据就绪: lynx={len(lynx_signals)} mindlynx={len(mindlynx_signals)} "
            f"tradingagent={len(ta_signals)}"
        )

        # 按股票池组织
        stock_signals = []
        for stock in self.stock_pool:
            code = stock["code"]
            lynx = lynx_signals.get(code, {})
            mindlynx = mindlynx_signals.get(code, {})
            ta = ta_signals.get(code.upper(), {})

            # lynx: signal 可能含 emoji 如 "🟢 买入"
            lynx_signal_raw = lynx.get("signal", "")
            lynx_prob_up = lynx.get("prob_up", 50.0)

            # MindLynx: signal/score 来自报告解析
            mindlynx_advice = mindlynx.get("signal", "")
            mindlynx_score = mindlynx.get("score", 50)

            # TradingAgent: rating 来自日志
            ta_rating = ta.get("rating", "")

            # 至少有一个系统有真实数据才加入
            has_data = bool(lynx_signal_raw) or bool(mindlynx_advice) or bool(ta_rating)

            # 获取最新股价和涨跌幅（直接调 Sina 实时行情 API，不依赖缓存）
            price = 0.0
            pct_chg = 0.0
            volume_ratio = 0.0
            ma5 = ma10 = ma20 = 0.0
            price_fetched = False
            try:
                prefix = 'sh' if code.startswith(('6', '5', '9')) else 'sz'
                url = f'https://hq.sinajs.cn/list={prefix}{code}'
                resp = requests.get(url, headers={'Referer': 'https://finance.sina.com.cn'}, timeout=10)
                if resp.status_code == 200:
                    parts = resp.text.split(',')
                    if len(parts) >= 4:
                        # 返回格式: 股票名,今开,昨收,当前价,最高,最低,...
                        prev_close = float(parts[2]) if parts[2] else 0.0
                        price = float(parts[3]) if parts[3] else 0.0
                        if prev_close > 0:
                            pct_chg = round((price - prev_close) / prev_close * 100, 2)
                            price_fetched = True
            except Exception:
                pass

            # 降级：实时行情失败时，从 lynx 日K线获取（收盘后安全，有内容校验）
            if not price_fetched and self.lynx._ensure_imported():
                try:
                    df_price = self.lynx._lynx_module.fetch_daily_bars(code)
                    if df_price is not None and len(df_price) >= 2:
                        last = df_price.iloc[-1]
                        prev = df_price.iloc[-2]
                        price = float(last.get("收盘", 0))
                        prev_close = float(prev.get("收盘", 0))
                        if prev_close > 0:
                            pct_chg = round((price - prev_close) / prev_close * 100, 2)
                            price_fetched = True
                except Exception:
                    pass

            # 从 stock_daily DB 获取更丰富的技术指标（量比、均线）
            try:
                import sqlite3
                db_path = "systems/MindLynx-Aistock/data/stock_analysis.db"
                db = sqlite3.connect(db_path)
                row = db.execute(
                    f"SELECT volume_ratio, ma5, ma10, ma20 FROM stock_daily "
                    f"WHERE code=? ORDER BY date DESC LIMIT 1", (code,)
                ).fetchone()
                if row:
                    volume_ratio = round(row[0], 2) if row[0] else 0.0
                    ma5 = round(row[1], 2) if row[1] else 0.0
                    ma10 = round(row[2], 2) if row[2] else 0.0
                    ma20 = round(row[3], 2) if row[3] else 0.0
                db.close()
            except Exception:
                pass

            if has_data:
                stock_signals.append({
                    "code": code,
                    "name": stock["name"],
                    "price": price,
                    "pct_chg": pct_chg,
                    "volume_ratio": volume_ratio,
                    "ma5": ma5,
                    "ma10": ma10,
                    "ma20": ma20,
                    "lynx_signal": lynx_signal_raw if lynx_signal_raw else "观望",
                    "lynx_prob_up": float(lynx_prob_up) if lynx_prob_up else 50.0,
                    "mindlynx_advice": mindlynx_advice if mindlynx_advice else "观望",
                    "mindlynx_score": int(mindlynx_score) if mindlynx_score else 50,
                    "mindlynx_valid": bool(mindlynx_advice),
                    "tradingagent_rating": ta_rating if ta_rating else "Hold",
                    "tradingagent_valid": bool(ta_rating),
                })

        logger.info(f"UnifiedDataLoader: 组织完成 {len(stock_signals)} 只股票")
        return stock_signals
