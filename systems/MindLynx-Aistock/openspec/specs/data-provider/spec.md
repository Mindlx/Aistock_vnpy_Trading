## Purpose

The Data Provider subsystem fetches market data (daily OHLCV, realtime quotes, fundamentals, sector data, chip distribution, news) from 17 external sources organized in a configurable priority chain. It provides a unified interface through `DataFetcherManager` with automatic failover, circuit breaking, and graceful degradation. A single stock's data failure MUST NOT block analysis of other stocks.

## Requirements

### Requirement: Data fetching SHALL use a configurable priority chain
The system SHALL attempt data providers in priority order for each data type (daily K-line, realtime quote, fundamentals). If the highest-priority provider fails, the next SHALL be tried automatically. The priority order SHALL be configurable via env vars (`DAILY_DATA_PRIORITY`, `REALTIME_QUOTE_PRIORITY`).

#### Scenario: Primary provider unavailable
- **WHEN** `efinance_fetcher` (P0 for daily data) fails with a network error
- **THEN** the system SHALL try `akshare_fetcher` (P1), then `tushare_fetcher`/`pytdx_fetcher` (P2), then `baostock_fetcher` (P3), then `yfinance_fetcher` (P4), then `longbridge_fetcher` (P5)

#### Scenario: All providers fail for a stock
- **WHEN** every provider in the chain fails for a single stock
- **THEN** the system SHALL log the error and move to the next stock — single-stock failure SHALL NOT block the overall analysis

### Requirement: The system SHALL normalize stock codes across markets
Stock codes SHALL follow a canonical format: A-share (pure numeric, e.g., `600519`), HK stocks (`hk00700`), US stocks (`AAPL`). The `canonical_stock_code()` function SHALL normalize any input format to this standard. YFinance code conversion SHALL map A-share codes to `.SS`/`.SZ` suffix format as needed.

#### Scenario: HK stock code normalization
- **WHEN** a user provides `HK00700` or `hk00700`
- **THEN** the system SHALL normalize it to `hk00700`

#### Scenario: A-share code normalization
- **WHEN** a user provides `600519`
- **THEN** the system SHALL treat it as an A-share code with no prefix

### Requirement: Realtime quotes SHALL support HTTP polling and WebSocket
The system SHALL provide realtime quote data through two mechanisms: HTTP polling with a priority chain (Tencent → akshare_sina → efinance → akshare_em) and WebSocket streaming via EastMoney push2 (A-share only). WebSocket SHALL be disabled by default and configurable via `WEBSOCKET_REALTIME_ENABLED`. On WebSocket failure, the system SHALL fall back to HTTP polling.

#### Scenario: WebSocket disconnect
- **WHEN** the EastMoney WebSocket connection drops during a monitoring session
- **THEN** the system SHALL log the error and fall back to HTTP polling without disrupting the analysis flow

### Requirement: Rate limiting and anti-blocking measures SHALL be applied
The system SHALL implement exponential backoff for retries after provider failures. The EastMoney patch SHALL inject a NID token and random User-Agent to reduce rate limiting. Tushare free-tier SHALL protect `cyq_chips` and `trade_cal` endpoints with rate-limit awareness.

#### Scenario: Rate limit hit
- **WHEN** a provider returns a rate-limit response
- **THEN** the system SHALL wait with exponential backoff, then retry before falling through to the next provider

### Requirement: Stock name resolution SHALL be cached
The system SHALL prefetch stock names in batch before concurrent analysis to avoid redundant per-stock name resolution calls. Name resolution SHALL use a lightweight endpoint and fall back gracefully if unavailable.

#### Scenario: Stock name prefetch
- **WHEN** the pipeline starts analyzing 5 or more stocks
- **THEN** it SHALL call `prefetch_stock_names()` to batch-resolve all stock names before parallel analysis
