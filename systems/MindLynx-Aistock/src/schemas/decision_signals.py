"""Decision Signal schemas.

Pydantic models for skill-level decision signal persistence and retrieval.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DecisionSignalCreate(BaseModel):
    """Payload for creating a single decision signal record."""

    stock_code: str = Field(..., description="Stock code (e.g. 600519, AAPL)")
    stock_name: str = Field("", description="Stock display name")
    skill_id: str = Field(..., description="Skill identifier, e.g. 'bull_trend' or 'consensus'")
    signal: str = Field(..., description="Signal value: strong_buy/buy/hold/sell/strong_sell")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    conditions_met: list[str] = Field(default_factory=list, description="Conditions that were satisfied")
    conditions_missed: list[str] = Field(default_factory=list, description="Conditions that were not satisfied")
    score_adjustment: float = Field(0.0, description="Score adjustment -20 to +20")
    reasoning: str = Field("", description="Skill evaluation reasoning text")
    analysis_date: datetime | None = Field(None, description="Analysis date; defaults to now")
    query_id: str = Field("", description="Query correlation ID for tracing")


class DecisionSignalItem(BaseModel):
    """A single decision signal record as returned by the API."""

    id: int | None = None
    stock_code: str
    stock_name: str = ""
    skill_id: str
    signal: str
    confidence: float
    conditions_met: list[str] = Field(default_factory=list)
    conditions_missed: list[str] = Field(default_factory=list)
    score_adjustment: float = 0.0
    reasoning: str = ""
    analysis_date: datetime | None = None
    query_id: str = ""
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class DecisionSignalSummary(BaseModel):
    """Aggregated signal summary for a stock."""

    stock_code: str
    stock_name: str
    total_signals: int = 0
    signal_counts: dict[str, int] = Field(default_factory=dict, description="Counts by signal type")
    avg_confidence: float = 0.0
    latest_signal: str | None = None
    latest_confidence: float | None = None
    latest_skill_id: str | None = None
    latest_analysis_date: datetime | None = None
    skill_breakdown: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-skill latest signal snapshot",
    )
