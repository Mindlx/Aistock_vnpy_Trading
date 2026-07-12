## ADDED Requirements

### Requirement: SignalLoader loads LY signal from UnifiedCache

The system SHALL provide a `SignalLoader` class that loads LY (Lynx) quantitative signals from `UnifiedCache` for a given stock code.

#### Scenario: LY signal exists in cache
- **WHEN** `SignalLoader.load_ly_signal("601801")` is called and cached data exists
- **THEN** it returns a formatted markdown string with position, score, and price data

#### Scenario: LY signal not in cache
- **WHEN** `SignalLoader.load_ly_signal("601801")` is called and no cached data exists
- **THEN** it returns a fallback string indicating no LY signal available

### Requirement: SignalLoader loads ML factor from stock_analysis.db

The system SHALL provide a `SignalLoader.load_ml_factor` method that reads ML factor data from `stock_analysis.db`.

#### Scenario: ML factor data exists
- **WHEN** `SignalLoader.load_ml_factor("601801")` is called and DB has records
- **THEN** it returns formatted factor values and model prediction

#### Scenario: ML factor data absent
- **WHEN** `SignalLoader.load_ml_factor("601801")` is called and DB has no records
- **THEN** it returns a fallback string indicating no ML factor

### Requirement: SignalLoader caches per-stock results within session

The system SHALL cache loaded signals per stock code within a single session to avoid redundant queries.

#### Scenario: Same stock queried twice
- **WHEN** `SignalLoader.load_ly_signal("601801")` is called twice
- **THEN** the second call returns the cached result without re-querying the cache
