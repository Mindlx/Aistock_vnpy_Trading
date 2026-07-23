"""Decision Signal API schemas.

Pydantic response models for the decision signal endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.schemas.decision_signals import DecisionSignalItem, DecisionSignalSummary


class DecisionSignalResponse(DecisionSignalItem):
    """Response model for a single decision signal."""

    pass


class DecisionSignalListResponse(BaseModel):
    """Response model for listing decision signals."""

    items: list[DecisionSignalResponse] = Field(default_factory=list)
    total: int = 0


class DecisionSignalSummaryResponse(BaseModel):
    """Response model for aggregated signal summary."""

    stock_code: str
    stock_name: str = ""
    total_signals: int = 0
    signal_counts: dict[str, int] = Field(default_factory=dict)
    avg_confidence: float = 0.0
    latest_signal: str | None = None
    latest_confidence: float | None = None
    latest_skill_id: str | None = None
    latest_analysis_date: datetime | None = None
    skill_breakdown: list[dict[str, Any]] = Field(default_factory=list)
