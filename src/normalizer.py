"""
三系统信号统一归一化模块 — 7 级语义对齐 (v3.1)

⚠️ 仅供学习和研究目的，不构成任何投资建议

根据 Oracle + c1skill 论证，三系统映射已校准到统一锚点。

    级别        ly (概率)         ml (操作)      at (研报)
    +3 强烈看多  prob_up≥75%      买入           Buy
    +2 看多      prob_up≥65%      加仓           Overweight
     +1 谨慎看多  prob_up≥59%      —              —
      0 中性/持有 prob_up≈50%      持有/观望      Hold
     -1 谨慎看空  prob_up≥35%      减仓           Underweight
     -2 看空      prob_up≥25%      卖出           —
     -3 强烈看空  prob_up<25%      —              Sell

映射方式: 分段线性（flat 中性区 42~52%→0.00，锚点间线性插值）
vs v3.0: 从 logit+tanh 改为分段线性，ml/at 值对齐设计目标

关键语义修正: "持有"映射到 L7=0（中性），不再当作弱看多信号。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Optional, Tuple


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

# 连续得分→标签阈值（single source of truth for all modules）
L7_THRESHOLDS = {
    "strong_bullish": 2.5,
    "bullish": 1.5,
    "cautious_bullish": 1.0,
    "cautious_bearish": -0.5,
    "bearish": -1.5,
    "strong_bearish": -2.5,
}

L7_LABELS = {
    3: "strong_bullish",
    2: "bullish",
    1: "cautious_bullish",
    0: "neutral",
    -1: "cautious_bearish",
    -2: "bearish",
    -3: "strong_bearish",
}

# ↓ 以下三个字典以 L7_LABELS 的值为 key，各模块 import 使用 ↓

L7_SIGNAL_NAMES = {
    "strong_bullish": "强烈看多",
    "bullish": "看多",
    "cautious_bullish": "谨慎看多",
    "neutral": "中性/持有",
    "cautious_bearish": "谨慎看空",
    "bearish": "看空",
    "strong_bearish": "强烈看空",
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

L7_EMOJI = {
    "strong_bullish": "🚀",
    "bullish": "📈",
    "cautious_bullish": "↗️",
    "neutral": "➡️",
    "cautious_bearish": "↘️",
    "bearish": "📉",
    "strong_bearish": "🚨",
}

GROUP_ICONS = {
    "strong_bullish": "🚀",
    "bullish": "📈",
    "cautious_bullish": "📈",
    "neutral": "🗂",
    "cautious_bearish": "📉",
    "bearish": "📉",
    "strong_bearish": "🚨",
}

# 分歧状态下仓位上限
MAX_POSITION_DISAGREEMENT = "1成"


class SignalNormalizer:
    """三系统信号统一归一化 — 7级语义对齐 (v3.1)"""

    # ══════════════════════════════════════════
    # ly: 连续概率 → 分段线性映射（v3.1）
    # 原 logit+tanh（v3.0）已废弃
    # ══════════════════════════════════════════

    # emoji 剥离（兼容旧版 lynx_signal 输出）
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
        "买入": 3.0,      # L7=+3 (S1 强烈看多)
        "加仓": 2.06,     # L7=+2 (S2 看多)
        "持有": 0.0,      # L7=0（中性）
        "观望": -0.1,     # L7=0 ~ -1
        "减仓": -1.13,    # L7=-1 (S5 谨慎看空)
        "卖出": -2.06,    # L7=-2 (S6 看空)
    }

    # 评分微调系数（每系统不同）
    ML_SENTIMENT_FACTOR = {
        "买入": 0.55,     # base=3.0 × 0.18
        "加仓": 0.37,     # base=2.06 × 0.18
        "持有": 0.3,      # 窄幅，强化中性定位
        "观望": 0.3,
        "减仓": 0.20,     # base=1.13 × 0.18
        "卖出": 0.37,     # base=2.06 × 0.18
    }

    # ══════════════════════════════════════════
    # at: 离散评级 → 直接 L7 映射
    # 无数值输出，纯粹分类
    # ══════════════════════════════════════════

    TRADINGAGENT_L7_MAP: dict[str, float] = {
        "Buy": 3.0,           # L7=+3 (S1 强烈看多)
        "Overweight": 2.06,   # L7=+2 (S2 看多)
        "Hold": 0.0,          # L7=0 (中性/持有)
        "Underweight": -1.13, # L7=-1 (S5 谨慎看空)
        "Sell": -3.0,         # L7=-3 (S7 强烈看空)
    }

    # ────────── emoji 剥离 ──────────

    @classmethod
    def _strip_emoji(cls, text: str) -> str:
        return cls.EMOJI_PATTERN.sub("", text).strip()

    # ────────── ly: 连续概率归一化 ──────────

    @classmethod
    def normalize_lynx(cls, signal: str, prob_up: float) -> Tuple[float, bool]:
        """
        ly 归一化: raw prob_up → L7 score (线性缩放)。

        2026-07-09: 从锚点映射改为线性缩放。
        原锚点表校准在 12/18 只股票上降低了准确率 (49.4% vs raw 51.7%)，
        因此退回直接使用 prob_up 的方向信息。

        映射方式: prob_up (0~100) → L7 score (-3~+3)
          prob_up=0   → -3.00
          prob_up=50  →  0.00  (方向分界点)
          prob_up=100 → +3.00

        参数:
            signal: 原始信号（兼容旧格式，当前仅用于有效性判断）
            prob_up: 上涨概率 0-100
        返回:
            (L7 得分, 是否有效)
        """
        p = float(prob_up)
        if p < 0 or p > 100:
            return 0.0, False

        # raw prob_up → L7 线性映射 (-3 ~ +3)
        score = (p / 100.0) * 6.0 - 3.0
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
        - "买入" → base=3.0 (v3.1), 评分越高越强
        - "卖出" → base=-2.06 (v3.1), 评分越低越弱

        Flat 降权 (2026-06-07):
        sentiment_score 在 41-59 (flat zone) 时，LLM 方向信号弱(1.8% acc)。
        整体系数乘 0.5 降低其对融合得分的影响。

        参数:
            operation_advice: 操作建议
            sentiment_score: LLM 评分 0-100
            trend_prediction: 趋势预测（保留参数，暂未使用）

        返回:
            L7 得分 (-3 ~ +3)
        """
        if math.isnan(sentiment_score):
            return 0.0
        base = cls.ML_BASE.get(operation_advice, 0.0)
        factor = cls.ML_SENTIMENT_FACTOR.get(operation_advice, 0.3)
        modulation = factor * (sentiment_score - 50.0) / 50.0
        score = base + modulation
        # Flat zone 降权: score 41-59 时 LLM 方向信号弱
        if 41 <= sentiment_score <= 59:
            score *= 0.5
        return max(-3.0, min(3.0, score))

    # ────────── at: 离散评级归一化 ──────────

    @classmethod
    def normalize_mindlynx_score(cls, sentiment_score: int, threshold_bull: int = 52, threshold_bear: int = 49) -> float:
        """
        Accuracy-calibrated sentiment_score → L7 mapping (v4.0).

        Based on 598-sample backtest & c1skill cross-disciplinary analysis
        (2026-06-29, docs/decisions/accuracy-calibrated-mapping.md):

        ≤19:  100.0% acc → -3.0 (S7 strong_bearish)
        20-30: 89.0% acc → -2.5 (S6/S7 boundary)
        31-40: 92.8% acc → -2.0 (S6 bearish)
        41-48: 92.8% acc → -1.5 (S5 cautious_bearish, preserved)
        50-51:  0.0% acc →  0.0 (S4 neutral)
        52-59: 56.2% acc → +0.8 (S4+, barely bullish, conservative)
        60-79: 38.2% acc → +1.0 (S3 cautious_bullish, dampened)
        ≥80:   (extrap)   → +1.5 (S2-, extrapolated)

        Design principles (L1 book sources):
        - Grinold & Kahn *APM* IR=IC×√BR: precision(IC) > granularity(BR)
        - López de Prado *AFML* meta-labeling: asymmetric accuracy→asymmetric mapping
        - Carver *Systematic Trading*: continuous signals beat discrete bins
        - Kahneman *TFS* WYSIATI: classification jumps lose gradient info
        - Taleb *Black Swan*: each discretization boundary creates a Platonic fold

        52-59 / 60-79 tiers use conservative parameters due to limited
        samples (76 / 59). Re-evaluate via Phase 2 monitor when N≥300/tier.

        Args:
            sentiment_score: LLM score 0-100
            threshold_bull: score >= this → bullish (default 52)
            threshold_bear: score <= this → bearish (default 49)

        返回: L7 得分 (-3 ~ +3)
        """
        s = sentiment_score

        # Flat zone — LLM has zero directional accuracy here (0.0% acc)
        if threshold_bear < s < threshold_bull:
            return 0.0

        # ── Bearish regime (high accuracy 89-100%, sufficient samples) ──
        if s <= threshold_bear:
            if s <= 19:
                return -3.0    # 100.0% acc, 9 samples → S7
            if s <= 30:
                return -2.5    #  89.0% acc, 93 samples → S6/S7 boundary
            if s <= 40:
                return -2.0    #  92.8% acc, bulk → S6
            return -1.5        #  92.8% acc, 41-48, 351 samples → S5 (kept)

        # ── Bullish regime (low accuracy 38-56%, ⚠️ limited samples 59-76) ──
        if s >= threshold_bull:
            if s >= 80:
                return +1.5    # extrapolated → S2 boundary
            if s >= 60:
                return +1.0    # 38.2% acc, 59 samples → S3 (dampened)
            return +0.8        # 56.2% acc, 76 samples → S4+ (conservative, not +1.0)

        return 0.0

    @classmethod
    def normalize_tradingagent(cls, rating: str, debate_state: Optional[Dict[str, Any]] = None) -> float:
        """
        at 归一化: 5 级分类 → 连续 L7 映射（v3.2）。

        基础映射:
          Buy → +3.0 (L7=+3 强烈看多)
          Overweight → +2.06 (L7=+2 看多)
          Hold → 0.0 (L7=0 中性)
          Underweight → -1.13 (L7=-1 谨慎看空)
          Sell → -3.0 (L7=-3 强烈看空)

        辩论共识对分数做平滑拉伸:
          共识越强(agreement→1.0)信号越接近极端值,
          共识越弱(agreement→0.5)信号越接近基础值。
          分析师分歧(analyst_variance)越高, 拉伸幅度越小。
        """
        normalized = rating.strip().capitalize()
        base = cls.TRADINGAGENT_L7_MAP.get(normalized, 0.0)
        if debate_state and debate_state.get("debate_available"):
            agreement = debate_state.get("investment_agreement", 0.5)
            variance = debate_state.get("analyst_variance", 0.5)
            # 共识偏离中性的程度(0.5~1.0→0.0~1.0)
            boost = max(0.0, (agreement - 0.5) * 2.0)
            # 分歧衰减: 高分歧时拉伸幅度折半
            decay = 1.0 - min(variance, 0.5) * 0.5
            effective = boost * decay
            # 目标值(基础值与更极端值之间做插值)
            if normalized == "Underweight":
                base = -1.13 + (-2.06 + 1.13) * effective  # -1.13 → -2.06
            elif normalized == "Overweight":
                base = 2.06 + (3.0 - 2.06) * effective      # +2.06 → +3.0
            elif normalized == "Buy":
                base = 2.5 + (3.0 - 2.5) * effective        # +2.5 → +3.0
            elif normalized == "Sell":
                base = -2.5 + (-3.0 + 2.5) * effective      # -2.5 → -3.0
        return round(base, 4)

    # ────────── 原始信号解析辅助 ──────────

    @classmethod
    def parse_lynx_signal_text(cls, signal_text: str) -> str:
        """从含 emoji 的信号文本中提取纯信号名"""
        return cls._strip_emoji(signal_text)

    @classmethod
    def map_normalized_to_label(cls, score: float) -> str:
        """将 L7 得分映射到可读标签 (阈值与 L7_THRESHOLDS / _get_final_decision 一致)"""
        if score > 2.5:
            return "strong_bullish"
        elif score > 1.5:
            return "bullish"
        elif score > 1.0:
            return "cautious_bullish"
        elif score > -0.5:
            return "neutral"
        elif score > -1.5:
            return "cautious_bearish"
        elif score > -2.5:
            return "bearish"
        else:
            return "strong_bearish"

    @classmethod
    def score_to_l7_integer(cls, score: float) -> int:
        """将连续 L7 得分映射到 7 级整数 (阈值与 L7_THRESHOLDS 一致)"""
        if score > 2.5:
            return 3
        elif score > 1.5:
            return 2
        elif score > 1.0:
            return 1
        elif score > -0.5:
            return 0
        elif score > -1.5:
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

    # ────────── 分歧仓位上限 ──────────

    @staticmethod
    def cap_position_for_disagreement(position: str) -> str:
        """分歧状态下限制仓位上限"""
        if "2-3" in position or "1-2" in position or "0.5-1" in position:
            return MAX_POSITION_DISAGREEMENT
        if "0.5成以内" in position or "减仓" in position:
            return "减仓至0.5成以内"
        if "清仓" in position or "0成" in position:
            return position
        return "0.5成以内"

    # ────────── v4.0 统一仓位接口（百分比，替代旧版"成"） ──────────

    @staticmethod
    def l7_target_pct(signal_label: str) -> float:
        """L7 信号 → 建议仓位百分比（% of 总资金）。"""
        from src.position import L7_TARGET_PCT
        return L7_TARGET_PCT.get(signal_label, 0.0)

    @staticmethod
    def l7_target_label(signal_label: str) -> str:
        """L7 信号 → 仓位中文标签。"""
        from src.position import L7_TARGET_LABEL
        return L7_TARGET_LABEL.get(signal_label, "空仓")

    @staticmethod
    def l7_target_range(signal_label: str) -> tuple[float, float]:
        """L7 信号 → 仓位百分比范围 (min, max)。"""
        from src.position import L7_TARGET_PCT_RANGE
        return L7_TARGET_PCT_RANGE.get(signal_label, (0, 0))

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
