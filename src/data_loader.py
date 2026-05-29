"""
三系统输出数据加载适配器（零侵入版）

核心原则：不修改三个原有系统的任何代码。
所有读取逻辑完全自包含在 fusion_system 内部。

数据获取方式：
- lynx_vnpy:     通过 Python import 直接调用 lynx_signal.py 的导出函数
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

    使用方式:
        loader = LynxDataLoader()
        signals = loader.load_by_date("2026-05-29")
        # 返回: {"601801": {"signal": "🟢 买入", "prob_up": 72.0, ...}, ...}
    """

    def __init__(self, lynx_project_root: str = "systems/lynx_vnpy"):
        """
        参数:
            lynx_project_root: lynx_vnpy 项目根目录的相对路径
        """
        self.lynx_root = Path(lynx_project_root).expanduser().resolve()
        self._imported = False
        self._lynx_module = None

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

    def load_by_date(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """
        通过直接调用 lynx_signal.py 的导出函数生成信号。

        不调用 run()（会打印+推送），而是逐只股票调用：
          fetch_daily_bars → compute_features → predict_signal

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

        logger.info(f"lynx_vnpy: 开始获取 {len(stock_codes)} 只股票信号...")
        results = {}

        for code in stock_codes:
            try:
                # 1. 获取日K数据（lynx_signal.py 使用新浪财经 API）
                df = lynx.fetch_daily_bars(code)
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

                # API 限流保护
                time.sleep(2)

            except Exception as e:
                logger.error(f"lynx_vnpy [{code}]: 异常 {e}")
                continue

        logger.info(f"lynx_vnpy: 完成，{len(results)}/{len(stock_codes)} 只获得信号")
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
        # 宽松匹配: 允许 NAME 中包含零个或多个 * 前缀
        pattern = re.compile(
            r"^[🟢🟡🔴⚪]\s+\*{0,2}(.+?)\((\w+)\)\*\s*:\s*(\S+)\s*\|\s*评分\s*(\d+)",
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

        参数:
            stock_code: 股票代码（自动转大写作为 ticker）
            date_str: 日期 YYYY-MM-DD

        返回:
            {"rating": "Buy"/..., "price_target": float|None, "full_decision": str}
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

    def _build_path_patterns(self, ticker: str, date_str: str) -> List[Path]:
        """生成可能的日志文件路径（兼容多种日期格式）"""
        base = self.logs_dir / ticker / "MindTradingAgentStrategy_logs"
        date_variants = [
            date_str,                          # 2026-05-29
            date_str.replace("-", ""),         # 20260529
        ]
        return [
            base / f"full_states_log_{d}.json"
            for d in date_variants
        ]

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

        return {
            "rating": rating,
            "price_target": price_target,
            "full_decision": final_decision,
        }

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

            if has_data:
                stock_signals.append({
                    "code": code,
                    "name": stock["name"],
                    "lynx_signal": lynx_signal_raw if lynx_signal_raw else "观望",
                    "lynx_prob_up": float(lynx_prob_up) if lynx_prob_up else 50.0,
                    "mindlynx_advice": mindlynx_advice if mindlynx_advice else "观望",
                    "mindlynx_score": int(mindlynx_score) if mindlynx_score else 50,
                    "tradingagent_rating": ta_rating if ta_rating else "Hold",
                })

        logger.info(f"UnifiedDataLoader: 组织完成 {len(stock_signals)} 只股票")
        return stock_signals
