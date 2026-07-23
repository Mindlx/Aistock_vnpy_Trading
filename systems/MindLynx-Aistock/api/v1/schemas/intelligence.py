"""Intelligence API schemas.

Pydantic models for RSS/Atom intelligence source CRUD and item listing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IntelligenceSourceCreateRequest(BaseModel):
    """Request body for creating a new intelligence source."""

    name: str = Field(..., min_length=1, max_length=100, description="Display name of the source")
    source_type: str = Field("rss", max_length=32, description="Source type: rss / atom / newsnow")
    url: str = Field(..., min_length=1, max_length=1000, description="Feed URL")
    enabled: bool = True
    scope_type: str = Field("market", max_length=32, description="Scope category: market / sector / stock")
    scope_value: str | None = Field(None, max_length=64, description="Scope value, e.g. market name or stock code")
    market: str = Field("cn", max_length=32, description="Market: cn / hk / us")
    description: str | None = Field(None, description="Optional description")


class IntelligenceSourceUpdateRequest(BaseModel):
    """Request body for updating an existing intelligence source."""

    name: str | None = Field(None, min_length=1, max_length=100)
    source_type: str | None = Field(None, max_length=32)
    url: str | None = Field(None, min_length=1, max_length=1000)
    enabled: bool | None = None
    scope_type: str | None = Field(None, max_length=32)
    scope_value: str | None = Field(None, max_length=64)
    market: str | None = Field(None, max_length=32)
    description: str | None = None


class IntelligenceSourceItem(BaseModel):
    """Response model for a single intelligence source."""

    id: int
    name: str
    source_type: str
    url: str
    enabled: bool
    scope_type: str
    scope_value: str | None = None
    market: str
    description: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_fetched_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    item_count: int = 0


class IntelligenceSourceListResponse(BaseModel):
    """Response model for listing intelligence sources."""

    items: list[IntelligenceSourceItem] = Field(default_factory=list)
    total: int


class IntelligenceItemResponse(BaseModel):
    """Response model for a single fetched intelligence item."""

    id: int
    source_id: int | None = None
    source_name: str | None = None
    source_type: str
    title: str
    summary: str | None = None
    url: str
    source: str | None = None
    published_at: str | None = None
    fetched_at: str | None = None
    scope_type: str
    scope_value: str
    market: str


class IntelligenceItemListResponse(BaseModel):
    """Response model for listing intelligence items."""

    items: list[IntelligenceItemResponse] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class IntelligenceFetchRequest(BaseModel):
    """Request body for triggering a fetch. If source_id is omitted, fetch all enabled sources."""

    source_id: int | None = Field(None, description="Specific source ID to fetch, or None for all enabled")


class IntelligenceFetchStatus(BaseModel):
    """Status of a single source fetch operation."""

    source_id: int
    source_name: str
    status: str
    items_fetched: int
    error: str | None = None


class IntelligenceFetchResponse(BaseModel):
    """Response model for a fetch operation."""

    results: list[IntelligenceFetchStatus] = Field(default_factory=list)


class IntelligenceSourceTestRequest(BaseModel):
    """Request body for testing a source URL without saving."""

    url: str = Field(..., min_length=1, max_length=1000, description="Feed URL to test")


class IntelligenceSourceTestResponse(BaseModel):
    """Response model for a source URL test."""

    success: bool
    title: str | None = None
    description: str | None = None
    entries_count: int = 0
    sample_entries: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class IntelligenceCreateDefaultsResponse(BaseModel):
    """Response model for creating default built-in sources."""

    created: int = 0
    sources: list[IntelligenceSourceItem] = Field(default_factory=list)


__all__ = [
    "IntelligenceSourceCreateRequest",
    "IntelligenceSourceUpdateRequest",
    "IntelligenceSourceItem",
    "IntelligenceSourceListResponse",
    "IntelligenceItemResponse",
    "IntelligenceItemListResponse",
    "IntelligenceFetchRequest",
    "IntelligenceFetchResponse",
    "IntelligenceFetchStatus",
    "IntelligenceSourceTestRequest",
    "IntelligenceSourceTestResponse",
    "IntelligenceCreateDefaultsResponse",
]
