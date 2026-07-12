## ADDED Requirements

### Requirement: ContextPreparer assembles multi-source context for injection

The system SHALL provide a `ContextPreparer` class that assembles market data, news, sentiment, and fundamentals context into a structured payload for LLM injection.

#### Scenario: All sources available
- **WHEN** `ContextPreparer.prepare_all("601801")` is called and all data sources respond
- **THEN** it returns a dict with keys: `market_context`, `news_context`, `sentiment_context`, `fundamentals_context`, `ly_signals_context`, `ml_factor_context`

### Requirement: ContextPreparer handles partial data gracefully

The system SHALL tolerate individual data source failures without crashing the entire context assembly.

#### Scenario: News source fails
- **WHEN** the news data source raises an exception during `prepare_news_context`
- **THEN** `news_context` is set to a fallback string and other contexts remain populated

### Requirement: ContextPreparer builds injection payload for LLM

The system SHALL provide a `build_injection_payload` method that formats the assembled context into the system message and AIMessage structure expected by the LLM debate flow.

#### Scenario: Injection payload generated
- **WHEN** `ContextPreparer.build_injection_payload(context_dict)` is called
- **THEN** it returns a tuple of (system_message_text, ai_message_text) matching the existing `_injected_create` format
