## ADDED Requirements

### Requirement: StockAnalyzer computes technical rating from history

The system SHALL provide a `StockAnalyzer` class with a `technical_rating` method that computes a technical rating (Buy/Hold/Sell) from a price history DataFrame.

#### Scenario: Bullish technical indicators
- **WHEN** `StockAnalyzer.technical_rating(hist)` is called with a DataFrame showing upward trends, volume support, and strong technicals
- **THEN** it returns a rating of `"Buy"` or `"StrongBuy"` with supporting rationale

#### Scenario: Bearish technical indicators
- **WHEN** `StockAnalyzer.technical_rating(hist)` is called with a DataFrame showing downward trends and weak technicals
- **THEN** it returns a rating of `"Sell"` or `"StrongSell"` with supporting rationale

#### Scenario: Neutral or mixed indicators
- **WHEN** `StockAnalyzer.technical_rating(hist)` is called with mixed signals
- **THEN** it returns `"Hold"` with balanced rationale

### Requirement: StockAnalyzer provides fallback analysis via yfinance

The system SHALL provide a `fallback_analysis` method that fetches price data via yfinance and computes a simple technical signal, replicating the existing `_fallback_akshare` logic.

#### Scenario: yfinance data available
- **WHEN** `StockAnalyzer.fallback_analysis("601801", "2026-06-01")` is called and yfinance returns valid data
- **THEN** it returns a result dict with `rating`, `change_pct`, and `success=True`

#### Scenario: yfinance data unavailable
- **WHEN** `StockAnalyzer.fallback_analysis("601801", "2026-06-01")` is called and yfinance returns no data
- **THEN** it returns an empty result with `success=False` and descriptive error
