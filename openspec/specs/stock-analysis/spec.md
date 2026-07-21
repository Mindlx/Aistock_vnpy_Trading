# stock-analysis Specification

## Purpose
StockAnalyzer 提供技术面评分和降级分析。`technical_rating` 基于 OHLCV 历史数据计算 6 维度评分（价格 vs 均线、均线排列、RSI、MACD、量价配合）输出 5 级评级（Buy/Overweight/Hold/Underweight/Sell）。`fallback_analysis` 在 TradingAgent 分析失败时通过 yfinance 获取数据并计算技术评分作为降级方案。

## Requirements
### Requirement: StockAnalyzer computes technical rating from history

The system SHALL provide a `StockAnalyzer` class with a `technical_rating` method that computes a technical rating (Buy/Overweight/Hold/Underweight/Sell) from a price history DataFrame.

#### Scenario: Bullish technical indicators
- **WHEN** `StockAnalyzer.technical_rating(hist)` is called with a DataFrame showing upward trends, volume support, and strong technicals
- **THEN** it returns a rating of `"Buy"` or `"Overweight"` with supporting rationale

#### Scenario: Bearish technical indicators
- **WHEN** `StockAnalyzer.technical_rating(hist)` is called with a DataFrame showing downward trends and weak technicals
- **THEN** it returns a rating of `"Sell"` or `"Underweight"` with supporting rationale

#### Scenario: Neutral or mixed indicators
- **WHEN** `StockAnalyzer.technical_rating(hist)` is called with mixed signals
- **THEN** it returns `"Hold"` with balanced rationale

### Requirement: StockAnalyzer provides fallback analysis via yfinance

The system SHALL provide a `fallback_analysis` method that fetches price data via yfinance and computes a simple technical signal, replicating the existing fallback logic.

#### Scenario: yfinance data available
- **WHEN** `StockAnalyzer.fallback_analysis("601801", "2026-06-01")` is called and yfinance returns valid data
- **THEN** it returns a result dict with `rating`, `change_pct`, and `success=True`

#### Scenario: yfinance data unavailable
- **WHEN** `StockAnalyzer.fallback_analysis("601801", "2026-06-01")` is called and yfinance returns no data
- **THEN** it returns an empty result with `success=False` and descriptive error
