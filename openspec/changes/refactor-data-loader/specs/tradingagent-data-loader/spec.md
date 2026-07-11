## ADDED Requirements

### Requirement: TradingAgentDataLoader parses debate logs
The system SHALL provide a `TradingAgentDataLoader` class in its own file that parses TradingAgent debate state logs. Interface identical to the existing class.

#### Scenario: Load by stock and date
- **WHEN** `TradingAgentDataLoader().load_by_stock_and_date("601801", "2026-07-01")` is called
- **THEN** it returns parsed debate state matching the original format
