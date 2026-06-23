"""
Low-sensitivity public market-phase summary for compact notification display.

Provides format_public_market_status_line() which produces a single-line
market/phase indicator such as "A股 · 盘中" or "US · Intraday".
"""

from __future__ import annotations

from typing import Any

from src.core.trading_calendar import MarketPhase

_MARKET_STATUS_PREFIX = {
    "zh": "市场状态",
    "en": "Market status",
}

_MARKET_LABELS_ZH = {
    "cn": "A股",
    "hk": "港股",
    "us": "美股",
}

_MARKET_LABELS_EN = {
    "cn": "A-shares",
    "hk": "Hong Kong",
    "us": "US",
}

_PHASE_LABELS_ZH = {
    MarketPhase.PREMARKET: "盘前",
    MarketPhase.INTRADAY: "盘中",
    MarketPhase.LUNCH_BREAK: "午间休市",
    MarketPhase.CLOSING_AUCTION: "临近收盘",
    MarketPhase.POSTMARKET: "盘后",
    MarketPhase.NON_TRADING: "非交易日",
    MarketPhase.UNKNOWN: "阶段未知",
}

_PHASE_LABELS_EN = {
    MarketPhase.PREMARKET: "Pre-market",
    MarketPhase.INTRADAY: "Intraday",
    MarketPhase.LUNCH_BREAK: "Lunch break",
    MarketPhase.CLOSING_AUCTION: "Near close",
    MarketPhase.POSTMARKET: "Post-market",
    MarketPhase.NON_TRADING: "Non-trading",
    MarketPhase.UNKNOWN: "Unknown phase",
}


def format_public_market_status_line(
    phase: MarketPhase | None,
    *,
    market: str | None = None,
    language: str = "zh",
) -> str:
    """Format one compact market/phase line for aggregate reports.

    Args:
        phase: Current market phase (from infer_market_phase).
        market: Market region key ('cn', 'hk', 'us', or None).
        language: 'zh' or 'en' for output language.

    Returns:
        Formatted string like "A股 · 盘中", or empty string if insufficient data.
    """
    if not phase or phase == MarketPhase.UNKNOWN:
        return ""

    lang = "en" if str(language or "").lower().startswith("en") else "zh"
    phase_labels = _PHASE_LABELS_EN if lang == "en" else _PHASE_LABELS_ZH
    market_labels = _MARKET_LABELS_EN if lang == "en" else _MARKET_LABELS_ZH

    phase_label = phase_labels.get(phase, phase.value)

    if market and market in market_labels:
        market_label = market_labels[market]
        value = f"{market_label} · {phase_label}"
    else:
        value = phase_label

    separator = ": " if lang == "en" else "："
    return f"{_MARKET_STATUS_PREFIX[lang]}{separator}{value}"
