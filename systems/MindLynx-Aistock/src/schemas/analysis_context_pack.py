"""
===================================
Analysis Context Pack — Pydantic Models
===================================

Structured, validated models for analysis context snapshots.

Replaces the inline dict assembly in pipeline.py _build_context_snapshot()
with type-safe Pydantic models that document the expected shape,
provide sensible defaults for missing data, and enable structured
logging / serialisation downstream.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MarketPhaseContext(BaseModel):
    """Trading session phase for the stock's market."""

    phase: str = "unknown"
    """Machine-readable phase label: premarket / intraday / lunch_break / closing_auction / postmarket / non_trading / unknown"""

    market: str = ""
    """Market region: cn / hk / us / jp / kr"""

    is_open: bool = False
    """Whether the market is currently in a trading session (intraday or closing_auction)."""

    session_label: str = ""
    """Human-readable phase description, e.g. '盘中', '盘前', '非交易日'."""


class TechnicalContext(BaseModel):
    """Technical indicators extracted from enhanced_context / today / trend_analysis."""

    ma5: float | None = None
    """5-period moving average"""
    ma10: float | None = None
    """10-period moving average"""
    ma20: float | None = None
    """20-period moving average"""
    ma50: float | None = None
    """50-period moving average"""
    volume_ratio: float | None = None
    """Volume ratio (today volume / 5d avg volume)"""
    rsi: float | None = None
    """Relative Strength Index (optional)"""
    macd: float | None = None
    """MACD histogram value (optional)"""
    ma_status: str | None = None
    """MA alignment description, e.g. '多头排列', '空头排列', '震荡整理'"""


class FundamentalContext(BaseModel):
    """Fundamental / valuation indicators from enhanced_context."""

    pe: float | None = None
    """Price-to-Earnings ratio (TTM)"""
    pb: float | None = None
    """Price-to-Book ratio"""
    roe: float | None = None
    """Return on Equity (%), computed as pb / pe * 100 when both available"""
    market_cap: float | None = None
    """Total market capitalisation (yuan / local currency)"""


class SentimentContext(BaseModel):
    """News / sentiment summary derived from news_content."""

    news_sentiment: float = Field(default=0.5, ge=0.0, le=1.0)
    """Aggregated sentiment score — 0 = negative, 0.5 = neutral, 1 = positive"""
    news_count: int = 0
    """Number of news items found"""
    top_news: list[str] = Field(default_factory=list)
    """Headlines of the top N news items (truncated to ~60 chars each)"""


class ChipContext(BaseModel):
    """Chip (holding-cost) distribution context, primarily for A-shares."""

    concentration: str | None = None
    """Concentration description, e.g. '高度集中', '较集中', '较分散'"""
    cost_distribution: list[float] | None = None
    """Cost distribution boundaries [lower_90, upper_90, lower_70, upper_70]"""
    chip_avg_cost: float | None = None
    """Weighted average cost of all holders"""


class AnalysisSnapshot(BaseModel):
    """
    Complete structured analysis context for a single stock.
    All fields have sensible defaults — every sub-context is optional.
    """

    stock_code: str = ""
    """Normalised stock code, e.g. '600519', 'HK00700'"""
    stock_name: str = ""
    """Human-readable stock name"""
    market_phase: MarketPhaseContext = Field(default_factory=MarketPhaseContext)
    technical: TechnicalContext = Field(default_factory=TechnicalContext)
    fundamental: FundamentalContext = Field(default_factory=FundamentalContext)
    sentiment: SentimentContext = Field(default_factory=SentimentContext)
    chip: ChipContext = Field(default_factory=ChipContext)
    enhanced_raw: dict[str, Any] = Field(default_factory=dict)
    """Full enhanced_context dict for backward compatibility"""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    """ISO-8601 timestamp of when this snapshot was built"""
