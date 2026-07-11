## ADDED Requirements

### Requirement: MLFactorLoader loads ML factor signals
The system SHALL provide an `MLFactorLoader` class in its own file. Interface identical to the existing class.

#### Scenario: Load ML factors by date
- **WHEN** `MLFactorLoader().load_by_date("2026-07-01")` is called
- **THEN** it returns signal dict matching the original format
