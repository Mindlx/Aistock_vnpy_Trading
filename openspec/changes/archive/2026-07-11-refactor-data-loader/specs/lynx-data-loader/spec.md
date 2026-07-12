## ADDED Requirements

### Requirement: LynxDataLoader loads RF quantitative signals
The system SHALL provide a `LynxDataLoader` class in its own file that loads Lynx/RF quantitative signals for a given date. Interface identical to the existing class.

#### Scenario: Load signals by date
- **WHEN** `LynxDataLoader().load_by_date("2026-07-01")` is called
- **THEN** it returns a dict of stock_code → signal data matching the original format
