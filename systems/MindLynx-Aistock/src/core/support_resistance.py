"""Mathematical support/resistance level computation.

Replaces LLM "guessing" with verifiable methods:
  1. Bollinger Bands (lower=支撑, upper=压力)
  2. Prior highs/lows (20d/60d)
  3. Moving averages (MA20/MA60)
  4. Fibonacci retracement (swing high→low)
  5. VWAP (volume-weighted average price)

Output: compact list of levels ready for mobile push reports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PriceLevel:
    price: float
    label: str  # "布林下轨" / "MA20" / "前高" / "Fib61.8%"
    strength: str  # "strong" / "medium" / "weak"


def compute_levels(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float] | None = None,
    current_price: float | None = None,
) -> tuple[list[PriceLevel], list[PriceLevel]]:
    """Compute support and resistance levels.

    Args:
        closes: historical closes (ascending)
        highs: historical highs
        lows: historical lows
        volumes: historical volumes (optional, for VWAP)
        current_price: current price (default: last close)

    Returns:
        (supports, resistances) — each sorted by strength then proximity.
    """
    n = len(closes)
    if n < 20:
        return [], []

    supports: list[PriceLevel] = []
    resistances: list[PriceLevel] = []
    cp = current_price or closes[-1]

    # 1. Bollinger Bands (20 period)
    import statistics

    window20 = closes[-20:]
    ma20 = statistics.mean(window20)
    std20 = statistics.stdev(window20) if len(window20) > 1 else 0
    bb_lower = ma20 - 2 * std20
    bb_upper = ma20 + 2 * std20
    if bb_lower < cp:
        supports.append(
            PriceLevel(round(bb_lower, 2), "布林下轨", "strong" if cp - bb_lower < bb_lower * 0.1 else "medium")
        )
    if bb_upper > cp:
        resistances.append(
            PriceLevel(round(bb_upper, 2), "布林上轨", "strong" if bb_upper - cp < cp * 0.1 else "medium")
        )

    # 2. Moving averages
    ma20 = round(statistics.mean(closes[-20:]), 2) if n >= 20 else 0
    ma60 = round(statistics.mean(closes[-60:]), 2) if n >= 60 else 0
    if ma20 and ma20 < cp:
        supports.append(PriceLevel(ma20, "MA20", "strong"))
    elif ma20 > cp:
        resistances.append(PriceLevel(ma20, "MA20", "strong"))
    if ma60 and ma60 < cp:
        supports.append(PriceLevel(ma60, "MA60", "medium"))
    elif ma60 > cp:
        resistances.append(PriceLevel(ma60, "MA60", "medium"))

    # 3. Prior highs/lows
    if n >= 20:
        high20 = max(highs[-20:])
        low20 = min(lows[-20:])
        if high20 > cp:
            resistances.append(PriceLevel(round(high20, 2), "20日高点", "medium"))
        if low20 < cp:
            supports.append(PriceLevel(round(low20, 2), "20日低点", "medium"))
    if n >= 60:
        high60 = max(highs[-60:])
        low60 = min(lows[-60:])
        if high60 > cp:
            resistances.append(PriceLevel(round(high60, 2), "60日高点", "strong"))
        if low60 < cp:
            supports.append(PriceLevel(round(low60, 2), "60日低点", "strong"))

    # 4. Fibonacci retracement (from recent swing high→low)
    if n >= 60:
        recent = closes[-60:]
        swing_high = max(recent)
        swing_low = min(recent)
        range_val = swing_high - swing_low
        if range_val > 0 and swing_low < cp < swing_high:
            fib_382 = swing_high - range_val * 0.382
            fib_500 = swing_high - range_val * 0.500
            fib_618 = swing_high - range_val * 0.618
            for fib_price, fib_label in [
                (fib_618, "Fib61.8%"),
                (fib_500, "Fib50.0%"),
                (fib_382, "Fib38.2%"),
            ]:
                fib_price = round(fib_price, 2)
                if fib_price < cp:
                    supports.append(PriceLevel(fib_price, fib_label, "medium"))
                elif fib_price > cp:
                    resistances.append(PriceLevel(fib_price, fib_label, "medium"))

    # 5. VWAP (volume-weighted average price, last 20 bars)
    if volumes and len(volumes) >= 20:
        vwap20 = (
            round(
                sum(closes[-20:][i] * volumes[-20:][i] for i in range(20)) / sum(volumes[-20:]),
                2,
            )
            if sum(volumes[-20:]) > 0
            else 0
        )
        if vwap20 and vwap20 < cp:
            supports.append(PriceLevel(vwap20, "VWAP20", "medium"))
        elif vwap20 > cp:
            resistances.append(PriceLevel(vwap20, "VWAP20", "medium"))

    # Sort: supports nearest to price first
    supports.sort(key=lambda s: (s.strength != "strong", abs(cp - s.price)))
    resistances.sort(key=lambda r: (r.strength != "strong", abs(r.price - cp)))

    return supports[:4], resistances[:4]


def format_levels(supports: list[PriceLevel], resistances: list[PriceLevel]) -> str:
    """Format levels as a compact mobile-friendly string."""
    parts = []
    if supports:
        parts.append("支撑 " + " · ".join(f"¥{s.price}({s.label})" for s in supports[:3]))
    if resistances:
        parts.append("压力 " + " · ".join(f"¥{r.price}({r.label})" for r in resistances[:3]))
    return " | ".join(parts) if parts else ""
