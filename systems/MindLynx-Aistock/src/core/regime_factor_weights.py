"""
Regime-conditional factor weight tables.

Maps (trend, volatility) → {factor_name: weight}. Used by FactorEngine
to dynamically adjust composite score weights based on current market regime.

Design constraints (c1skill Stage 4):
- Max weight adjustment per factor: ±40% of base weight
- Adjacent regime weights use gradient (not binary) transitions
- Fallback: sideways_mid_vol (closest to base) when regime data unavailable

Phase 1: hardcoded weights from theoretical priors.
Phase 3: recalibrate from regime-stratified IC tracking.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 9×12 Regime Weight Table
# ═══════════════════════════════════════════════════════════════════════════════
# Keys: "{trend}_{vol_level}"  e.g. "uptrend_low_vol"
# Weights are RELATIVE; composite normalization handles absolute scale.
# Adjustment limit: ±40% from base CORE_FACTORS weight (c1skill §5.2).

REGIME_WEIGHTS: dict[str, dict[str, float]] = {

    # ── uptrend_low_vol: 趋势明确+低波动 → 动量主导，反转衰减 ──
    "uptrend_low_vol": {
        "momentum_reversal": 0.21,       # -40%, 反转在强趋势中成为噪音
        "momentum_spread": 0.42,         # +20%, 趋势加速信号
        "low_volatility": 0.10,          # -17%
        "volume_trend": 0.14,            # +40%, 量价配合确认趋势
        "turnover_sentiment": 0.05,      # -37%
        "price_position": 0.02,
        "volume_acceleration": 0.04,
        "consecutive_direction": 0.028,
        "volatility_ratio": 0.012,
        "size_factor": 0.056,# +50% → +40% capped, 小盘在低波牛市中领涨
        "illiquidity": 0.03,
        "max_effect": 0.024,
    },

    # ── uptrend_mid_vol: 正常上涨 → 接近基准 ──
    "uptrend_mid_vol": {
        "momentum_reversal": 0.28,
        "momentum_spread": 0.40,         # +14%
        "low_volatility": 0.12,
        "volume_trend": 0.12,
        "turnover_sentiment": 0.06,
        "price_position": 0.03,
        "volume_acceleration": 0.04,
        "consecutive_direction": 0.028,
        "volatility_ratio": 0.02,
        "size_factor": 0.04,
        "illiquidity": 0.04,
        "max_effect": 0.03,
    },

    # ── uptrend_high_vol: 上涨但波动大 → 波动率因子上升 ──
    "uptrend_high_vol": {
        "momentum_reversal": 0.21,       # -40%, 高波趋势中反转不可靠
        "momentum_spread": 0.21,         # -40%, 高波时动量信号噪声大
        "low_volatility": 0.168,# +40% capped, 低波防御
        "volume_trend": 0.06,
        "turnover_sentiment": 0.11,      # +37%
        "price_position": 0.02,
        "volume_acceleration": 0.02,
        "consecutive_direction": 0.02,
        "volatility_ratio": 0.028,# +50% → +40% capped
        "size_factor": 0.024,
        "illiquidity": 0.024,
        "max_effect": 0.056,# +50% → +40% capped
    },

    # ── downtrend_low_vol: 阴跌 → 反转捕捉底部 ──
    "downtrend_low_vol": {
        "momentum_reversal": 0.49,       # +40% capped, 反转最强
        "momentum_spread": 0.21,         # -40%, 趋势已破坏
        "low_volatility": 0.168,# +40% capped, 低波抗跌
        "volume_trend": 0.08,
        "turnover_sentiment": 0.10,
        "price_position": 0.04,
        "volume_acceleration": 0.02,
        "consecutive_direction": 0.012,
        "volatility_ratio": 0.028,# +50% → +40%
        "size_factor": 0.024,
        "illiquidity": 0.04,
        "max_effect": 0.056,
    },

    # ── downtrend_mid_vol: 正常下跌 ──
    "downtrend_mid_vol": {
        "momentum_reversal": 0.42,       # +20%
        "momentum_spread": 0.21,         # -40%
        "low_volatility": 0.168,# +40% capped
        "volume_trend": 0.06,
        "turnover_sentiment": 0.08,
        "price_position": 0.04,
        "volume_acceleration": 0.02,
        "consecutive_direction": 0.012,
        "volatility_ratio": 0.028,# +50% → +40%
        "size_factor": 0.024,
        "illiquidity": 0.04,
        "max_effect": 0.056,# +50% → +40%
    },

    # ── downtrend_high_vol: 恐慌下跌 → 波动率+极端收益因子 ──
    "downtrend_high_vol": {
        "momentum_reversal": 0.30,
        "momentum_spread": 0.21,         # -40%
        "low_volatility": 0.168,# +40% capped
        "volume_trend": 0.06,
        "turnover_sentiment": 0.06,
        "price_position": 0.04,
        "volume_acceleration": 0.018,
        "consecutive_direction": 0.012,
        "volatility_ratio": 0.028,# +50% → +40%
        "size_factor": 0.024,
        "illiquidity": 0.056,# +50% → +40%
        "max_effect": 0.056,# +50% → +40%
    },

    # ── sideways_low_vol: 窄幅震荡 → 均值回归+量价 ──
    "sideways_low_vol": {
        "momentum_reversal": 0.44,       # +26%
        "momentum_spread": 0.21,         # -40%, 震荡中趋势信号弱
        "low_volatility": 0.08,
        "volume_trend": 0.14,            # +40%, 量价信号在震荡中更可靠
        "turnover_sentiment": 0.06,
        "price_position": 0.04,
        "volume_acceleration": 0.04,
        "consecutive_direction": 0.028,
        "volatility_ratio": 0.012,
        "size_factor": 0.04,
        "illiquidity": 0.04,
        "max_effect": 0.024,
    },

    # ── sideways_mid_vol: 正常震荡（基准等价） ──
    "sideways_mid_vol": {
        "momentum_reversal": 0.35,
        "momentum_spread": 0.28,
        "low_volatility": 0.12,
        "volume_trend": 0.12,
        "turnover_sentiment": 0.08,
        "price_position": 0.03,
        "volume_acceleration": 0.03,
        "consecutive_direction": 0.02,
        "volatility_ratio": 0.02,
        "size_factor": 0.04,
        "illiquidity": 0.04,
        "max_effect": 0.04,
    },

    # ── sideways_high_vol: 高波震荡 → 低波动防御 ──
    "sideways_high_vol": {
        "momentum_reversal": 0.22,
        "momentum_spread": 0.21,         # -40%
        "low_volatility": 0.168,# +40% capped
        "volume_trend": 0.08,
        "turnover_sentiment": 0.11,
        "price_position": 0.03,
        "volume_acceleration": 0.02,
        "consecutive_direction": 0.02,
        "volatility_ratio": 0.028,# +50% → +40%
        "size_factor": 0.024,
        "illiquidity": 0.024,
        "max_effect": 0.056,# +50% → +40%
    },
}

# Default fallback regime (closest to CORE_FACTORS base weights)
_FALLBACK_REGIME = "sideways_mid_vol"


def make_regime_key(trend: str, volatility: str) -> str:
    """Build lookup key: 'uptrend_low_vol', 'downtrend_mid_vol', etc."""
    return f"{trend}_{volatility}"


def get_regime_weights(regime_key: str) -> dict[str, float] | None:
    """Look up factor weights for a regime key.

    Returns None only if key not found AND sideways_mid_vol also missing
    (should never happen — it's hardcoded).
    """
    weights = REGIME_WEIGHTS.get(regime_key)
    if weights is not None:
        return weights
    logger.warning("[RegimeWeights] 未知 regime key: %s，回退到 %s", regime_key, _FALLBACK_REGIME)
    return REGIME_WEIGHTS.get(_FALLBACK_REGIME)


def log_weight_diff(
    regime_key: str,
    applied_weights: dict[str, float],
    base_weights: dict[str, float],
) -> None:
    """Log weight deltas from base for transparency.

    Only logs factors with |delta| >= 0.02 to keep noise down.
    """
    diffs = []
    for name in sorted(applied_weights):
        new_w = applied_weights.get(name, 0.0)
        old_w = base_weights.get(name, 0.0)
        delta = new_w - old_w
        if abs(delta) >= 0.02:
            direction = "↑" if delta > 0 else "↓"
            diffs.append(f"{name}: {old_w:.2f}→{new_w:.2f} ({direction})")
    if diffs:
        logger.info(
            "[RegimeWeights] regime=%s — %d factors adjusted:\n  %s",
            regime_key,
            len(diffs),
            "\n  ".join(diffs),
        )
    else:
        logger.debug("[RegimeWeights] regime=%s — no significant adjustment", regime_key)
