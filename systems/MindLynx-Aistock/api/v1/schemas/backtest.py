"""Backtest API schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BacktestRunRequest(BaseModel):
    code: str | None = Field(None, description="仅回测指定股票")
    force: bool = Field(False, description="强制重新计算")
    eval_window_days: int | None = Field(None, ge=1, le=120, description="评估窗口（交易日数）")
    min_age_days: int | None = Field(None, ge=0, le=365, description="分析记录最小天龄（0=不限）")
    limit: int = Field(200, ge=1, le=2000, description="最多处理的分析记录数")


class BacktestRunResponse(BaseModel):
    processed: int = Field(..., description="候选记录数")
    saved: int = Field(..., description="写入回测结果数")
    completed: int = Field(..., description="完成回测数")
    insufficient: int = Field(..., description="数据不足数")
    errors: int = Field(..., description="错误数")


class BacktestResultItem(BaseModel):
    analysis_history_id: int
    code: str
    stock_name: str | None = None
    analysis_date: str | None = None
    eval_window_days: int
    engine_version: str
    eval_status: str
    evaluated_at: str | None = None
    operation_advice: str | None = None
    trend_prediction: str | None = None
    position_recommendation: str | None = None
    start_price: float | None = None
    end_close: float | None = None
    max_high: float | None = None
    min_low: float | None = None
    stock_return_pct: float | None = None
    actual_return_pct: float | None = None
    actual_movement: str | None = None
    direction_expected: str | None = None
    direction_correct: bool | None = None
    outcome: str | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    hit_stop_loss: bool | None = None
    hit_take_profit: bool | None = None
    first_hit: str | None = None
    first_hit_date: str | None = None
    first_hit_trading_days: int | None = None
    simulated_entry_price: float | None = None
    simulated_exit_price: float | None = None
    simulated_exit_reason: str | None = None
    simulated_return_pct: float | None = None


class BacktestResultsResponse(BaseModel):
    total: int
    page: int
    limit: int
    items: list[BacktestResultItem] = Field(default_factory=list)


class PerformanceMetrics(BaseModel):
    scope: str
    code: str | None = None
    eval_window_days: int
    engine_version: str
    computed_at: str | None = None

    total_evaluations: int
    completed_count: int
    insufficient_count: int
    long_count: int
    cash_count: int
    win_count: int
    loss_count: int
    neutral_count: int

    direction_accuracy_pct: float | None = None
    win_rate_pct: float | None = None
    neutral_rate_pct: float | None = None
    avg_stock_return_pct: float | None = None
    avg_simulated_return_pct: float | None = None

    stop_loss_trigger_rate: float | None = None
    take_profit_trigger_rate: float | None = None
    ambiguous_rate: float | None = None
    avg_days_to_first_hit: float | None = None

    advice_breakdown: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
