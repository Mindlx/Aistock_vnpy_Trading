# lynx-data-loader Specification

## Purpose
LynxDataLoader 从 lynx_vnpy 子系统加载 RandomForest + LightGBM 双模型量化信号。每个股票依次执行：统一缓存查询 → 技术指标计算 → RF/LGB 模型预测。支持多级缓存（UnifiedCache → Sina API）。

## Requirements
### Requirement: LynxDataLoader loads RF+LGB quantitative signals

The system SHALL provide a `LynxDataLoader` class that loads Lynx/RF quantitative signals for a given date.

#### Scenario: Load signals by date
- **WHEN** `LynxDataLoader().load_by_date("2026-07-01")` is called
- **THEN** it returns a dict of `{stock_code: {signal, prob_up, strength, ...}}` matching fusion engine format

#### Scenario: Cache hit avoids API call
- **WHEN** UnifiedCache has recent daily OHLCV for a stock
- **THEN** `_fetch_with_cache` returns cached data without calling Sina API

#### Scenario: Cache miss falls back to Sina
- **WHEN** UnifiedCache has no data or data is stale
- **THEN** `_fetch_with_cache` calls Sina API via lynx_signal's fetch_daily_bars

#### Scenario: Predict ensemble uses RF+LGB dual model
- **WHEN** `predict_ensemble` exists on lynx_signal module
- **THEN** both RF and LGB predictions are computed and combined

#### Scenario: Single model fallback
- **WHEN** `predict_ensemble` is not available on lynx_signal
- **THEN** falls back to RF-only prediction via `compute_features` + `predict_signal`
