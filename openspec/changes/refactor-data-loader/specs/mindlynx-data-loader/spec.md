## ADDED Requirements

### Requirement: MindLynxDataLoader loads analysis reports
The system SHALL provide a `MindLynxDataLoader` class in its own file that loads MindLynx analysis reports. Interface identical to the existing class.

#### Scenario: Load reports by date
- **WHEN** `MindLynxDataLoader().load_by_date("2026-07-01")` is called
- **THEN** it returns parsed report data matching the original format
