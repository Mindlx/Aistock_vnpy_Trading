"""Portfolio API schemas."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class PortfolioAccountCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    broker: str | None = Field(None, max_length=64)
    market: Literal["cn", "hk", "us"] = "cn"
    base_currency: str = Field("CNY", min_length=3, max_length=8)
    owner_id: str | None = Field(None, max_length=64)


class PortfolioAccountUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    broker: str | None = Field(None, max_length=64)
    market: Literal["cn", "hk", "us"] | None = None
    base_currency: str | None = Field(None, min_length=3, max_length=8)
    owner_id: str | None = Field(None, max_length=64)
    is_active: bool | None = None


class PortfolioAccountItem(BaseModel):
    id: int
    owner_id: str | None = None
    name: str
    broker: str | None = None
    market: str
    base_currency: str
    is_active: bool
    created_at: str | None = None
    updated_at: str | None = None


class PortfolioAccountListResponse(BaseModel):
    accounts: list[PortfolioAccountItem] = Field(default_factory=list)


class PortfolioTradeCreateRequest(BaseModel):
    account_id: int
    symbol: str = Field(..., min_length=1, max_length=16)
    trade_date: date
    side: Literal["buy", "sell"]
    quantity: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    fee: float = Field(0.0, ge=0)
    tax: float = Field(0.0, ge=0)
    market: Literal["cn", "hk", "us"] | None = None
    currency: str | None = Field(None, min_length=3, max_length=8)
    trade_uid: str | None = Field(None, max_length=128)
    note: str | None = Field(None, max_length=255)


class PortfolioCashLedgerCreateRequest(BaseModel):
    account_id: int
    event_date: date
    direction: Literal["in", "out"]
    amount: float = Field(..., gt=0)
    currency: str | None = Field(None, min_length=3, max_length=8)
    note: str | None = Field(None, max_length=255)


class PortfolioCorporateActionCreateRequest(BaseModel):
    account_id: int
    symbol: str = Field(..., min_length=1, max_length=16)
    effective_date: date
    action_type: Literal["cash_dividend", "split_adjustment"]
    market: Literal["cn", "hk", "us"] | None = None
    currency: str | None = Field(None, min_length=3, max_length=8)
    cash_dividend_per_share: float | None = Field(None, ge=0)
    split_ratio: float | None = Field(None, gt=0)
    note: str | None = Field(None, max_length=255)


class PortfolioEventCreatedResponse(BaseModel):
    id: int


class PortfolioDeleteResponse(BaseModel):
    deleted: int


class PortfolioTradeListItem(BaseModel):
    id: int
    account_id: int
    trade_uid: str | None = None
    symbol: str
    market: str
    currency: str
    trade_date: str
    side: str
    quantity: float
    price: float
    fee: float
    tax: float
    note: str | None = None
    created_at: str | None = None


class PortfolioTradeListResponse(BaseModel):
    items: list[PortfolioTradeListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PortfolioCashLedgerListItem(BaseModel):
    id: int
    account_id: int
    event_date: str
    direction: str
    amount: float
    currency: str
    note: str | None = None
    created_at: str | None = None


class PortfolioCashLedgerListResponse(BaseModel):
    items: list[PortfolioCashLedgerListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PortfolioCorporateActionListItem(BaseModel):
    id: int
    account_id: int
    symbol: str
    market: str
    currency: str
    effective_date: str
    action_type: str
    cash_dividend_per_share: float | None = None
    split_ratio: float | None = None
    note: str | None = None
    created_at: str | None = None


class PortfolioCorporateActionListResponse(BaseModel):
    items: list[PortfolioCorporateActionListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PortfolioPositionItem(BaseModel):
    symbol: str
    market: str
    currency: str
    quantity: float
    avg_cost: float
    total_cost: float
    last_price: float
    market_value_base: float
    unrealized_pnl_base: float
    unrealized_pnl_pct: float | None = None
    valuation_currency: str
    price_source: str = "unknown"
    price_provider: str | None = None
    price_date: str | None = None
    price_stale: bool = False
    price_available: bool = True


class PortfolioAccountSnapshot(BaseModel):
    account_id: int
    account_name: str
    owner_id: str | None = None
    broker: str | None = None
    market: str
    base_currency: str
    as_of: str
    cost_method: str
    total_cash: float
    total_market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fee_total: float
    tax_total: float
    fx_stale: bool
    positions: list[PortfolioPositionItem] = Field(default_factory=list)


class PortfolioSnapshotResponse(BaseModel):
    as_of: str
    cost_method: str
    currency: str
    account_count: int
    total_cash: float
    total_market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fee_total: float
    tax_total: float
    fx_stale: bool
    accounts: list[PortfolioAccountSnapshot] = Field(default_factory=list)


class PortfolioImportTradeItem(BaseModel):
    trade_date: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    fee: float
    tax: float
    trade_uid: str | None = None
    dedup_hash: str
    currency: str | None = None


class PortfolioImportParseResponse(BaseModel):
    broker: str
    record_count: int
    skipped_count: int
    error_count: int
    records: list[PortfolioImportTradeItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PortfolioImportCommitResponse(BaseModel):
    account_id: int
    record_count: int
    inserted_count: int
    duplicate_count: int
    failed_count: int
    dry_run: bool
    errors: list[str] = Field(default_factory=list)


class PortfolioImportBrokerItem(BaseModel):
    broker: str
    aliases: list[str] = Field(default_factory=list)
    display_name: str | None = None


class PortfolioImportBrokerListResponse(BaseModel):
    brokers: list[PortfolioImportBrokerItem] = Field(default_factory=list)


class PortfolioFxRefreshResponse(BaseModel):
    as_of: str
    account_count: int
    refresh_enabled: bool
    disabled_reason: str | None = None
    pair_count: int
    updated_count: int
    stale_count: int
    error_count: int


class PortfolioRiskResponse(BaseModel):
    as_of: str
    account_id: int | None = None
    cost_method: str
    currency: str
    thresholds: dict[str, Any] = Field(default_factory=dict)
    concentration: dict[str, Any] = Field(default_factory=dict)
    sector_concentration: dict[str, Any] = Field(default_factory=dict)
    drawdown: dict[str, Any] = Field(default_factory=dict)
    stop_loss: dict[str, Any] = Field(default_factory=dict)
