"""
三系统信号统一归一化模块

⚠️ 仅供学习和研究目的，不构成任何投资建议

根据三个系统实际输出格式进行归一化映射：
- lynx_vnpy: 信号含 emoji 前缀（如"🟢 买入"），置信度字段为 prob_up
- MindLynx_Aistock: operation_advice 含6种（买入/加仓/持有/减仓/卖出/观望），sentiment_score 0-100
- mind_TradingAgent: PortfolioRating 5级（Buy/Overweight/Hold/Underweight/Sell）
"""
from __future__ import annotations

import re
from typing import Optional, Tuple


class SignalNormalizer:
    """三系统信号统一归一化"""

    # ===== lynx_vnpy 映射（基于 lynx_signal.py 实际输出） =====
    # 原始信号含 emoji 前缀，如 "🟢 买入", "⚪ 观望", "🔴 回避"
    LYNX_SIGNAL_MAP: dict[str, float] = {
        "买入": 0.8,      # prob_up >= 65%，强烈看多
        "关注": 0.55,     # prob_up >= 55%，弱看多
        "观望": 0.0,      # prob_up >= 45%，中性
        "谨慎": -0.3,     # prob_up >= 35%，弱看空
        "回避": -0.8,     # prob_up < 35%，强烈看空
    }

    # ===== MindLynx_Aistock 映射 =====
    # 实际 operation_advice 有6种，这里分组合并
    MINDLYNX_SIGNAL_MAP: dict[str, float] = {
        "买入": 0.6,
        "加仓": 0.6,
        "持有": 0.6,      # 基础观点值，具体按评分细化
        "观望": 0.0,
        "减仓": -0.6,
        "卖出": -0.6,
    }

    # ===== mind_TradingAgent 映射 =====
    # 实际 PortfolioRating: Buy / Overweight / Hold / Underweight / Sell
    # 设计文档的 Strong BUY / BUY / HOLD / SELL / Strong SELL 为近似映射
    TRADINGAGENT_SIGNAL_MAP: dict[str, float] = {
        "Buy": 0.9,              # Strong BUY
        "Overweight": 0.5,       # BUY（偏多但不如 Buy 强烈）
        "Hold": 0.0,             # HOLD
        "Underweight": -0.5,     # SELL（偏空）
        "Sell": -0.9,            # Strong SELL
    }

    # ===== emoji 剥离 =====
    EMOJI_PATTERN = re.compile(r"^[🟢🟡🔴⚪]\s*")
    EMOJI_CHARS = re.compile(r"[\U0001F300-\U0001F9FF\u2600-\u27BF\u2B50\u2B06\u2B07\u2B05\u27A1\u2B55]")

    @classmethod
    def _strip_emoji(cls, text: str) -> str:
        """剥离文本开头的 emoji 和空格"""
        return cls.EMOJI_PATTERN.sub("", text).strip()

    # ────────── lynx_vnpy 归一化 ──────────

    @classmethod
    def normalize_lynx(cls, signal: str, prob_up: float) -> Tuple[float, bool]:
        """
        归一化 lynx_vnpy 输出。

        prob_up 是模型预测的上涨概率（0-100），不是置信度。
        prob_up 极端值（高或低）代表高确信度，中间值(45-55)代表不确定。
        调制规则：看多信号用 prob_up 加权，看空信号用 prob_down = (100-prob_up) 加权。

        参数:
            signal: 原始信号字符串，如 "🟢 买入", "⚪ 观望"
            prob_up: 上涨概率百分比（0-100）

        返回:
            (归一化得分, 是否有效)
        """
        # 剥离 emoji 前缀
        clean_signal = cls._strip_emoji(signal)
        base_score = cls.LYNX_SIGNAL_MAP.get(clean_signal, 0.0)

        # 观望信号：中性，不乘概率
        if clean_signal == "观望":
            return 0.0, True

        # 方向性置信度调制：
        #   看多信号 → 用 prob_up 加权（越高越看多）
        #   看空信号 → 用 prob_down = (100-prob_up) 加权（prob_up越低=越看空）
        if base_score > 0:
            conviction = prob_up / 100.0
        else:
            conviction = (100.0 - prob_up) / 100.0

        return base_score * conviction, True

    # ────────── MindLynx_Aistock 归一化 ──────────

    @classmethod
    def normalize_mindlynx(
        cls,
        operation_advice: str,
        sentiment_score: int,
        trend_prediction: Optional[str] = None,
    ) -> float:
        """
        归一化 MindLynx_Aistock 输出。

        参数:
            operation_advice: 操作建议（买入/加仓/持有/减仓/卖出/观望）
            sentiment_score: 综合评分（0-100）
            trend_prediction: 趋势预测（可选，用于辅助判断）

        返回:
            归一化得分（-0.6 ~ +0.6）
        """
        # 先按操作建议拿到基础观点值
        base_score = cls.MINDLYNX_SIGNAL_MAP.get(operation_advice, 0.0)

        if operation_advice in ("买入", "加仓"):
            # 买入信号: 评分越高越强
            if sentiment_score >= 70:
                return 0.6
            elif sentiment_score >= 60:
                return 0.5
            else:
                return 0.4  # 弱买入

        elif operation_advice == "持有":
            # 持有信号按评分区间细化
            if sentiment_score >= 60:
                return 0.6    # 明确持有
            elif sentiment_score >= 50:
                return 0.3    # 弱持有，谨慎偏多
            else:
                return 0.0    # 评分偏低，持有但偏弱

        elif operation_advice == "观望":
            # 观望按评分区分
            if sentiment_score >= 40:
                return 0.0    # 中性观望
            else:
                return -0.2   # 中性偏下

        elif operation_advice in ("减仓", "卖出"):
            return -0.6

        return 0.0

    # ────────── mind_TradingAgent 归一化 ──────────

    @classmethod
    def normalize_tradingagent(cls, rating: str) -> float:
        """
        归一化 mind_TradingAgent 输出。

        参数:
            rating: PortfolioRating 字符串
                    Buy / Overweight / Hold / Underweight / Sell

        返回:
            归一化得分（-0.9 ~ +0.9）

        注意:
            TradingAgent 不提供独立置信度字段，直接使用评级对应观点值。
        """
        # 输入标准化（首字母大写）
        normalized = rating.strip().capitalize()
        return cls.TRADINGAGENT_SIGNAL_MAP.get(normalized, 0.0)

    # ────────── 原始信号解析辅助 ──────────

    @classmethod
    def parse_lynx_signal_text(cls, signal_text: str) -> str:
        """从含 emoji 的信号文本中提取纯信号名"""
        return cls._strip_emoji(signal_text)

    @classmethod
    def map_normalized_to_label(cls, score: float) -> str:
        """将归一化得分映射到可读标签（用于日志/调试）"""
        if score >= 0.6:
            return "strong_bullish"
        elif score >= 0.2:
            return "bullish"
        elif score >= -0.2:
            return "neutral"
        elif score >= -0.6:
            return "bearish"
        else:
            return "strong_bearish"
