"""
三系统信号统一归一化模块 — 7 级语义对齐 (v3.0)

⚠️ 仅供学习和研究目的，不构成任何投资建议

根据 Oracle + c1skill 论证，三个系统参考系不同，采用 7 级统一决策空间：

    级别        ly (概率)         ml (操作)      at (研报)
    +3 强烈看多  logit 连续映射    买入           Buy
    +2 看多      ↑                加仓           Overweight
    +1 谨慎看多  ↑                —              —
     0 中性/持有 prob_up≈50%      持有/观望      Hold
    -1 谨慎看空  ↓                —              —
    -2 看空      ↓                减仓           Underweight
    -3 强烈看空  logit 连续映射    卖出           Sell

关键语义修正: "持有"映射到 L7=0（中性），不再当作弱看多信号。
"""
from __future__ import annotations

import math
import re
from typing import Optional, Tuple


# ══════════════════════════════════════════════
# 7 级统一决策空间常量
# ══════════════════════════════════════════════

L7_STRONG_BUY = 3.0
L7_BUY = 2.0
L7_CAUTIOUS_BUY = 1.0
L7_NEUTRAL = 0.0
L7_CAUTIOUS_SELL = -1.0
L7_SELL = -2.0
L7_STRONG_SELL = -3.0

L7_LABELS = {
    3: "strong_bullish",
    2: "bullish",
    1: "cautious_bullish",
    0: "neutral",
    -1: "cautious_bearish",
    -2: "bearish",
    -3: "strong_bearish",
}

L7_POSITION = {
    "strong_bullish": "2-3成",
    "bullish": "1-2成",
    "cautious_bullish": "0.5-1成",
    "neutral": "0成",
    "cautious_bearish": "减仓至0.5成以内",
    "bearish": "大幅减仓",
    "strong_bearish": "清仓",
}


class SignalNormalizer:
    """三系统信号统一归一化 — 7级语义对齐 (v3.0)"""

    # ══════════════════════════════════════════
    # ly: 连续概率 → logit+tanh 映射
    # 不再使用离散信号表。prob_up 是 sklearn 真实概率，
    # 用 logit 变换自然映射到 [-3, +3] 空间。
    # ══════════════════════════════════════════

    # emoji 剥离（仅用于兼容旧版 lynx_signal 输出）
    EMOJI_PATTERN = re.compile(r"^[🟢🟡🔴⚪]\s*")

    # ══════════════════════════════════════════
    # ml: 操作建议 → L7 映射
    # 关键修正: "持有"=L7=0，不再是弱看多
    # ══════════════════════════════════════════

    # 基础映射（仅用于参考，实际计算有评分微调）
    # 买入/加仓:  正 L7，sentiment_score 微调
    # 持有/观望:  L7=0，细微上下浮动
    # 减仓/卖出:  负 L7，sentiment_score 微调
    ML_BASE = {
        "买入": 2.2,      # L7=+3 ~ +2
        "加仓": 1.3,      # L7=+2 ~ +1
        "持有": 0.0,      # L7=0（中性）
        "观望": -0.1,     # L7=0 ~ -1
        "减仓": -1.7,     # L7=-2
        "卖出": -2.1,     # L7=-3 ~ -2
    }

    # 评分微调系数（每系统不同）
    ML_SENTIMENT_FACTOR = {
        "买入": 0.4,
        "加仓": 0.4,
        "持有": 0.3,      # 窄幅，强化中性定位
        "观望": 0.3,
        "减仓": 0.4,
        "卖出": 0.4,
    }

    # ══════════════════════════════════════════
    # at: 离散评级 → 直接 L7 映射
    # 无数值输出，纯粹分类
    # ══════════════════════════════════════════

    TRADINGAGENT_L7_MAP: dict[str, float] = {
        "Buy": 2.3,           # L7=+3 (强烈看多)
        "Overweight": 1.3,    # L7=+2 (看多)
        "Hold": 0.0,          # L7=0 (中性/持有)
        "Underweight": -1.3,  # L7=-2 (看空)
        "Sell": -2.3,         # L7=-3 (强烈看空)
    }

    # ────────── emoji 剥离 ──────────

    @classmethod
    def _strip_emoji(cls, text: str) -> str:
        return cls.EMOJI_PATTERN.sub("", text).strip()

    # ────────── ly: 连续概率归一化 ──────────

    @staticmethod
    def _logit(p: float) -> float:
        """logit 变换: ln(p / (1-p))，p∈(0,1)"""
        p = max(0.001, min(0.999, p / 100.0))
        return math.log(p / (1.0 - p))

    @classmethod
    def normalize_lynx(cls, signal: str, prob_up: float) -> Tuple[float, bool]:
        """
        ly 归一化: logit+tanh 连续映射。

        score = 3.0 × tanh(logit(prob_up) / 2.0)

        特性:
        - prob_up=50 → score=0.0（精确中性）
        - prob_up=70 → score≈1.2（L7=+2 看多）
        - prob_up=85 → score≈2.1（L7=+3 强烈看多）
        - 连续可微，无硬阈值

        参数:
            signal: 原始信号（兼容旧格式，当前仅用于有效性判断）
            prob_up: 上涨概率 0-100

        返回:
            (L7 得分, 是否有效)
        """
        p = float(prob_up)
        if p < 0 or p > 100:
            return 0.0, False
        logit_val = cls._logit(p)
        score = 3.0 * math.tanh(logit_val / 2.0)
        return score, True

    # ────────── ml: 操作建议归一化 ──────────

    @classmethod
    def normalize_mindlynx(
        cls,
        operation_advice: str,
        sentiment_score: int,
        trend_prediction: Optional[str] = None,
    ) -> float:
        """
        ml 归一化: 类别 + 评分微调。

        score = base(advice) + factor(advice) × (sentiment - 50) / 50

        关键语义:
        - "持有" → base=0.0, 评分仅在中性带内微调 (±0.3)
        - "买入" → base=2.2 向上，评分越高越强
        - "卖出" → base=-2.1 向下

        参数:
            operation_advice: 操作建议
            sentiment_score: LLM 评分 0-100
            trend_prediction: 趋势预测（保留参数，暂未使用）

        返回:
            L7 得分 (-3 ~ +3)
        """
        base = cls.ML_BASE.get(operation_advice, 0.0)
        factor = cls.ML_SENTIMENT_FACTOR.get(operation_advice, 0.3)
        modulation = factor * (sentiment_score - 50.0) / 50.0
        score = base + modulation
        return max(-3.0, min(3.0, score))

    # ────────── at: 离散评级归一化 ──────────

    @classmethod
    def normalize_tradingagent(cls, rating: str) -> float:
        """
        at 归一化: 直接 L7 映射。

        at 无数值输出，仅 5 级分类，直接映射到 L7:
          Buy → +2.3 (L7=+3)
          Overweight → +1.3 (L7=+2)
          Hold → 0.0 (L7=0)
          Underweight → -1.3 (L7=-2)
          Sell → -2.3 (L7=-3)
        """
        normalized = rating.strip().capitalize()
        return cls.TRADINGAGENT_L7_MAP.get(normalized, 0.0)

    # ────────── 原始信号解析辅助 ──────────

    @classmethod
    def parse_lynx_signal_text(cls, signal_text: str) -> str:
        """从含 emoji 的信号文本中提取纯信号名"""
        return cls._strip_emoji(signal_text)

    @classmethod
    def map_normalized_to_label(cls, score: float) -> str:
        """将 L7 得分映射到可读标签"""
        if score >= 2.5:
            return "strong_bullish"
        elif score >= 1.5:
            return "bullish"
        elif score >= 0.5:
            return "cautious_bullish"
        elif score >= -0.5:
            return "neutral"
        elif score >= -1.5:
            return "cautious_bearish"
        elif score >= -2.5:
            return "bearish"
        else:
            return "strong_bearish"

    @classmethod
    def score_to_l7_integer(cls, score: float) -> int:
        """将连续 L7 得分映射到 7 级整数"""
        if score >= 2.5:
            return 3
        elif score >= 1.5:
            return 2
        elif score >= 0.5:
            return 1
        elif score >= -0.5:
            return 0
        elif score >= -1.5:
            return -1
        elif score >= -2.5:
            return -2
        else:
            return -3

    @staticmethod
    def l7_label(l7: int) -> str:
        """L7 整数 → 标签"""
        return L7_LABELS.get(l7, "neutral")

    @staticmethod
    def l7_position(label: str) -> str:
        """标签 → 仓位建议"""
        return L7_POSITION.get(label, "0成")

    # ────────── 概率空间映射（贝叶斯融合） ──────────

    @staticmethod
    def to_probability(score: float, k: float = 1.0) -> float:
        """
        将 L7 得分 [-3, +3] 映射到概率 [0, 1]。

        v3.0: k 默认从 2.5 降至 1.0，适配 [-3,+3] 宽范围。

        k=1.0 时:
          +3.0 → P≈0.95
          +2.0 → P≈0.88
          +1.0 → P≈0.73
           0.0 → P=0.50
          -1.0 → P≈0.27
          -2.0 → P≈0.12
          -3.0 → P≈0.05
        """
        return 1.0 / (1.0 + math.exp(-k * score))
