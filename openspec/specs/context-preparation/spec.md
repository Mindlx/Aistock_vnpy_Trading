# context-preparation Specification

## Purpose
ContextPreparer 从多个数据源（LY 信号、ML 因子、行情、新闻、基本面）组装 LLM 注入上下文。SignalLoader 提供 LY/ML 信号字符串，本地方法提供行情/技术/新闻/基本面 Markdown。`prepare_all` 返回统一 context dict，`build_injection_payload` 格式化为单条 LLM 注入字符串。

## Requirements
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

### Requirement: ContextPreparer builds injection payload as single string

The system SHALL provide a `build_injection_payload` method that formats the assembled context into a single markdown string injected into the LLM's human message.

#### Scenario: Injection payload generated
- **WHEN** `ContextPreparer.build_injection_payload(context_dict)` is called
- **THEN** it returns a single formatted string containing all context sections with `[系统注入]` prefix
