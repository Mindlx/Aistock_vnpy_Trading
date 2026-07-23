"""
Compressed tool descriptions for B-version prompt optimization.

用法: 设置环境变量 USE_COMPACT_TOOLS=1 即可启用。
"""
from __future__ import annotations

import os

_COMPACT = os.environ.get("USE_COMPACT_TOOLS", "") == "1"

# Tool descriptions: (tool_name, A_version, B_version)
TOOL_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "analyze_trend": (
        "Run comprehensive technical trend analysis on a stock. "
        "Fetches historical data from database or data source. "
        "Returns MA alignment, bias rates, MACD status, RSI levels, "
        "volume analysis, support/resistance levels, and a buy/sell signal "
        "with a score (0-100).",
        "Technical trend analysis. Returns MA alignment, MACD, RSI, volume, support/resistance, "
        "and a buy/sell signal (0-100).",
    ),
    "calculate_ma": (
        "Calculate moving averages (MA5/10/20/30/60/120/250 or custom periods) for a stock. "
        "Returns each MA value, price bias %, and whether price is above each MA. "
        "Also returns overall MA alignment (多头/空头/混合).",
        "Calculate moving averages. Returns MA values, price bias %, and overall alignment.",
    ),
    "get_volume_analysis": (
        "Analyse volume-price relationship for a stock. "
        "Returns volume ratios, average volume on up vs down days, volume trend "
        "(expanding/shrinking), and pattern interpretation (量价配合/背离). "
        "Useful for confirming trend strength and detecting distribution or accumulation phases.",
        "Volume-price analysis. Returns volume ratios, trend (expanding/shrinking), "
        "and pattern interpretation. Confirms trend strength.",
    ),
    "analyze_pattern": (
        "Detect candlestick and chart patterns in recent price history. "
        "Identifies: Doji, Hammer, Shooting Star, Morning/Evening Star, Engulfing, "
        "Double Bottom(subtypes: 标准/高中间峰), upward breakout, box oscillation, and more. "
        "Returns pattern list with type (bullish/bearish/reversal), strength, and subtype_score. "
        "IMPORTANT: pattern='双底(高中间峰)' subtype_score=2.0 is the STRONGEST single signal "
        "(84.4% win rate, avg +14.3% in 20d, p<0.001). When detected, boost bullish conviction significantly.",
        "Detect candlestick and chart patterns. Returns patterns with type and strength. "
        "IMPORTANT: '双底(高中间峰)' (subtype_score=2.0) has 84.4% win rate — boost bullish conviction.",
    ),
    "get_realtime_quote": (
        "Get real-time stock quote including price, change%, volume ratio, "
        "turnover rate, PE, PB, market cap. Returns live market data.",
        "Get real-time quote: price, change%, volume ratio, turnover rate, PE/PB, market cap.",
    ),
    "get_daily_history": (
        "Get daily OHLCV (open, high, low, close, volume) historical data "
        "with MA5/MA10/MA20 indicators. Returns the last N trading days.",
        "Get daily OHLCV history with MA5/MA10/MA20. Returns last N trading days.",
    ),
    "get_chip_distribution": (
        "Get chip distribution analysis for a stock. "
        "Returns profit ratio, average cost, chip concentration at 90% and 70% levels. "
        "Useful for judging support/resistance and holding structure.",
        "Get chip distribution: profit ratio, avg cost, concentration (90%/70%). "
        "Useful for support/resistance and holding structure analysis.",
    ),
    "get_analysis_context": (
        "Get historical analysis context from the database for a stock. "
        "Returns today's and yesterday's OHLCV data, MA alignment status, "
        "volume and price changes. Provides the technical data foundation.",
        "Get historical analysis context: OHLCV, MA alignment, volume/price changes.",
    ),
    "search_stock_news": (
        "Search for the latest news articles about a specific stock. "
        "Requires both stock_code and stock_name for accurate search. "
        "Returns news titles, snippets, sources, and URLs.",
        "Search latest news for a stock. Requires stock_code and stock_name. "
        "Returns titles, snippets, sources, URLs.",
    ),
    "search_comprehensive_intel": (
        "Multi-dimensional intelligence search: latest news, market analysis, "
        "risk checking, earnings outlook, and industry trends for a stock. "
        "Returns a formatted report and structured results.",
        "Multi-dimensional intelligence: news, market analysis, risk check, earnings, industry trends.",
    ),
    "get_market_indices": (
        "Get major market indices (e.g., Shanghai Composite, Shenzhen Component, "
        "CSI 300 for China; S&P 500, Nasdaq, Dow for US). Provides market overview.",
        "Get major market indices (Shanghai Composite, S&P 500, etc.). Market overview.",
    ),
    "get_sector_rankings": (
        "Get sector/industry performance rankings. "
        "Returns top N and bottom N sectors by daily change percentage. "
        "Useful for sector rotation analysis.",
        "Get sector performance rankings. For sector rotation analysis.",
    ),
    "get_skill_backtest_summary": (
        "Inspect backtest data for a specific skill when skill-scoped stats exist. "
        "Provide skill_id for a targeted lookup; use get_strategy_backtest_summary for overall metrics. "
        "When skill-scoped rollups are unavailable, returns an informational response "
        "instead of fabricating metrics.",
        "Get backtest data for a specific skill (skill_id required). "
        "Read-only, does not fabricate metrics.",
    ),
    "get_strategy_backtest_summary": (
        "Legacy alias returning the overall backtest performance summary "
        "without triggering new backtests.",
        "Get overall backtest performance summary. Read-only.",
    ),
    "get_stock_backtest_summary": (
        "Get backtest performance data for a specific stock: per-stock summary "
        "(win rate, accuracy, avg return) plus recent evaluation records. "
        "Read-only, does not trigger new backtests.",
        "Get backtest data for a stock: win rate, accuracy, avg return. Read-only.",
    ),
}


def get_description(tool_name: str, a_description: str) -> str:
    """Return compact description if USE_COMPACT_TOOLS is set."""
    if not _COMPACT:
        return a_description
    pair = TOOL_DESCRIPTIONS.get(tool_name)
    if pair is None:
        return a_description
    return pair[1]


def patch_tool_descriptions(tools: list) -> None:
    """In-place patch tool descriptions to compact versions."""
    if not _COMPACT:
        return
    for tool in tools:
        b_desc = TOOL_DESCRIPTIONS.get(tool.name)
        if b_desc:
            tool.description = b_desc[1]
