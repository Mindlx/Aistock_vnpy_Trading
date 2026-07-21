# signal-loading Specification

## Purpose
SignalLoader 从 `data/realtime/` 文件交换区加载 LY（Lynx）和 ML（MindLynx）信号，格式化为 LLM 可读的 markdown 字符串。不包含行情/价格数据——price 由 ContextPreparer 的行情上下文独立获取。

## Requirements
### Requirement: SignalLoader loads LY signal from realtime JSON/CSV files

The system SHALL provide a `SignalLoader` class that loads LY (Lynx) quantitative signals from `data/realtime/ly_signal.json`, `data/realtime/ly_alpha_signal.json`, and `data/realtime/prob_up_log.csv`.

#### Scenario: LY RF JSON file exists with current data
- **WHEN** `SignalLoader.load_ly_signal("601801")` is called and `ly_signal.json` exists with timestamp within 36h
- **THEN** it returns a formatted markdown table with ensemble probability, RF probability, L7 score, and confidence level

#### Scenario: LY data stale (>36h)
- **WHEN** `SignalLoader.load_ly_signal("601801")` is called and `ly_signal.json` timestamp is older than 36 hours
- **THEN** the stale entry is ignored (treated as if absent)

#### Scenario: LY LGB JSON exists alongside RF JSON
- **WHEN** both `ly_signal.json` and `ly_alpha_signal.json` exist
- **THEN** output includes both RF and LGB model probabilities, plus model disagreement metric

#### Scenario: LY CSV log provides ensemble probability
- **WHEN** `prob_up_log.csv` exists with latest row containing `prob_up_ensemble`
- **THEN** ensemble probability is sourced from CSV (overrides JSON values)

#### Scenario: LY no data files
- **WHEN** none of the LY data files exist
- **THEN** it returns an empty string (caller treats as absent)

### Requirement: SignalLoader loads ML factor from realtime JSON

The system SHALL provide a `SignalLoader.load_ml_factor` method that reads ML factor data from `data/realtime/ml_signal.json`.

#### Scenario: ML factor data exists
- **WHEN** `SignalLoader.load_ml_factor("601801")` is called and `ml_signal.json` has record for stock
- **THEN** it returns formatted string with composite_score, l7_score, composite_label, and top 3 factors

#### Scenario: ML factor data absent
- **WHEN** `SignalLoader.load_ml_factor("601801")` is called and `ml_signal.json` has no record for stock
- **THEN** it returns an empty string

### Requirement: SignalLoader caches per-stock results within session

The system SHALL cache loaded signals per stock code within a single session to avoid redundant file reads.

#### Scenario: Same stock queried twice
- **WHEN** `SignalLoader.load_ly_signal("601801")` is called twice
- **THEN** the second call returns the cached result without re-reading files

#### Scenario: Cache per stock independent
- **WHEN** different stock codes are queried
- **THEN** each has independent cache entry

#### Scenario: Cache cleared per stock or all
- **WHEN** `SignalLoader.clear_cache("601801")` is called
- **THEN** only that stock's cache is removed
- **WHEN** `SignalLoader.clear_cache()` is called
- **THEN** all cache entries are removed

