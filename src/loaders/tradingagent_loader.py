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

    def __init__(self, logs_dir: str = "data/tradingagent/ta_logs/"):
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
