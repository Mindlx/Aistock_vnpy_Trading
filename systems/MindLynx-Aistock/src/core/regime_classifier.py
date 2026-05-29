"""Market regime classifier for adaptive parameter switching.

Classifies current market state into 9 regimes (3 trend directions × 3 volatility levels)
and provides recommended trading parameters for each.

Based on Adaptive Market Hypothesis (AMH): single parameter sets are unsustainable
in non-stationary markets like A-shares.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Regime definitions and recommended parameters
# ---------------------------------------------------------------------------

REGIME_PARAMS = {
    # (trend_direction, volatility_level) → {parameter_set}
    ("uptrend", "low_vol"): {
        "ma_short": 5, "ma_mid": 20, "ma_long": 60,
        "atr_mult": 2.0, "rsi_overbought": 75,
    },
    ("uptrend", "mid_vol"): {
        "ma_short": 8, "ma_mid": 20, "ma_long": 60,
        "atr_mult": 2.5, "rsi_overbought": 70,
    },
    ("uptrend", "high_vol"): {
        "ma_short": 10, "ma_mid": 30, "ma_long": 80,
        "atr_mult": 3.0, "rsi_overbought": 65,
    },
    ("downtrend", "low_vol"): {
        "ma_short": 5, "ma_mid": 15, "ma_long": 40,
        "atr_mult": 2.0, "rsi_oversold": 25,
    },
    ("downtrend", "mid_vol"): {
        "ma_short": 5, "ma_mid": 15, "ma_long": 40,
        "atr_mult": 2.5, "rsi_oversold": 30,
    },
    ("downtrend", "high_vol"): {
        "ma_short": 5, "ma_mid": 10, "ma_long": 30,
        "atr_mult": 3.0, "rsi_oversold": 35,
    },
    ("sideways", "low_vol"): {
        "ma_short": 10, "ma_mid": 30, "ma_long": 60,
        "atr_mult": 2.0, "rsi_overbought": 70,
    },
    ("sideways", "mid_vol"): {
        "ma_short": 15, "ma_mid": 30, "ma_long": 60,
        "atr_mult": 2.5, "rsi_overbought": 70,
    },
    ("sideways", "high_vol"): {
        "ma_short": 20, "ma_mid": 40, "ma_long": 80,
        "atr_mult": 3.0, "rsi_overbought": 65,
    },
}


REVERSE_REGIME_PARAMS = {
    (v["ma_short"], v["ma_mid"], v["ma_long"], v["atr_mult"]): k
    for k, v in REGIME_PARAMS.items()
}


LOW_VOL_THRESHOLD: float = 0.02   # ATR/price < 2%  → low vol
HIGH_VOL_THRESHOLD: float = 0.05  # ATR/price > 5%  → high vol


# ---------------------------------------------------------------------------
# Regime classifier
# ---------------------------------------------------------------------------

def classify_regime(
    close_prices: list[float],
    high_prices: list[float] | None = None,
    low_prices: list[float] | None = None,
    window: int = 20,
) -> dict:
    """Classify current market regime from price data.

    Regime = (trend_direction, volatility_level)
    - trend_direction: "uptrend" | "downtrend" | "sideways"
    - volatility_level: "low_vol" | "mid_vol" | "high_vol"

    Args:
        close_prices: historical close prices (ascending, at least `window` entries)
        high_prices: historical highs (for ATR calculation)
        low_prices: historical lows (for ATR calculation)
        window: lookback window for MA/ATR calculation (default 20)

    Returns:
        dict with regime info and recommended parameters.
    """
    if len(close_prices) < window:
        return _default_regime()

    closes = close_prices[-window:]

    # Trend direction via MA20 slope
    ma20 = sum(closes) / len(closes)
    ma20_prev = sum(close_prices[-window * 2:-window]) / max(1, len(close_prices[-window * 2:-window]))
    ma20_slope = (ma20 - ma20_prev) / ma20_prev if ma20_prev > 0 else 0.0

    if ma20_slope > 0.005:
        trend = "uptrend"
    elif ma20_slope < -0.005:
        trend = "downtrend"
    else:
        trend = "sideways"

    # Volatility level via ATR
    vol_level = "mid_vol"
    if high_prices and low_prices and len(high_prices) >= window and len(low_prices) >= window:
        trs = []
        for i in range(-window, 0):
            h = high_prices[i]
            l = low_prices[i]
            pc = close_prices[i - 1] if i > -len(close_prices) else h
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        atr = sum(trs) / len(trs) if trs else 0.0
        current_price = closes[-1]
        atr_ratio = atr / current_price if current_price > 0 else 0.0

        if atr_ratio < LOW_VOL_THRESHOLD:
            vol_level = "low_vol"
        elif atr_ratio > HIGH_VOL_THRESHOLD:
            vol_level = "high_vol"

    params = REGIME_PARAMS.get((trend, vol_level), REGIME_PARAMS[("sideways", "mid_vol")])

    return {
        "regime": f"{trend}_{vol_level}",
        "trend": trend,
        "volatility": vol_level,
        "ma20_slope": round(ma20_slope, 4),
        "current_price": closes[-1],
        "ma20": round(ma20, 2),
        "recommended_params": params,
    }


def _default_regime() -> dict:
    return {
        "regime": "sideways_mid_vol",
        "trend": "sideways",
        "volatility": "mid_vol",
        "ma20_slope": 0.0,
        "current_price": 0.0,
        "ma20": 0.0,
        "recommended_params": REGIME_PARAMS[("sideways", "mid_vol")],
    }


def build_regime_prompt(regime: dict) -> str:
    """Build a human-readable regime summary for LLM prompt injection."""
    p = regime["recommended_params"]
    trend = regime["trend"]
    vol = regime["volatility"]

    trend_cn = {"uptrend": "上升趋势", "downtrend": "下降趋势", "sideways": "震荡整理"}
    vol_cn = {"low_vol": "低波动", "mid_vol": "中等波动", "high_vol": "高波动"}

    lines = [
        "### 市场状态 (Market Regime)",
        f"- 趋势: {trend_cn.get(trend, trend)} (MA20斜率={regime['ma20_slope']:+.4f})",
        f"- 波动: {vol_cn.get(vol, vol)} (当前价={regime['current_price']})",
        f"- 推荐MA参数: ({p['ma_short']}, {p['ma_mid']}, {p['ma_long']})",
        f"- 推荐ATR倍数: {p['atr_mult']}x",
    ]
    # R3 fix: show correct RSI threshold based on regime direction
    if trend == "downtrend" and "rsi_oversold" in p:
        lines.append(f"- 推荐RSI超卖阈值: {p['rsi_oversold']}")
    else:
        lines.append(f"- 推荐RSI超买阈值: {p.get('rsi_overbought', 70)}")
    lines.extend([
        "",
        "> 上述参数基于AMH（适应性市场假说）按市场状态动态切换。",
    ])
    return "\n".join(lines)
