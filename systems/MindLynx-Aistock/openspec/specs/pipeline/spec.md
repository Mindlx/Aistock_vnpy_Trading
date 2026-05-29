## Purpose

The StockAnalysisPipeline orchestrates the end-to-end stock analysis flow. It coordinates data fetching, technical analysis, factor scoring, news intelligence, LLM reasoning, report generation, and notification delivery. The pipeline is the central nervous system — all modules connect through it. It supports both a traditional direct-LLM path and an agent-based path.

## Requirements

### Requirement: The pipeline SHALL be lazy-loaded to avoid import-order issues
`StockAnalysisPipeline` SHALL resolve on first attribute access via `__getattr__` descriptor, not at module import time. This prevents circular import issues caused by `main.py` calling `setup_env()` at module level.

#### Scenario: Pipeline import safety
- **WHEN** another module imports `main` at the top level before `setup_env()` has completed
- **THEN** the pipeline class SHALL NOT be resolved until the first attribute access, avoiding crashes from uninitialized dependencies

### Requirement: The pipeline SHALL execute in two phases — pre-compute then per-stock
Before analyzing individual stocks, the pipeline SHALL compute global context in batch: factor scores (via FactorEngine), market regime (via RegimeClassifier), portfolio allocation (via PortfolioOptimizer), and ATR position sizing (via PositionSizer). These pre-computed values SHALL be injected into each stock's analysis context.

#### Scenario: Factor pre-computation
- **WHEN** the pipeline starts with 8 stocks
- **THEN** it SHALL first compute factor scores for all 8 stocks as a single batch, then use these scores in each stock's individual analysis

### Requirement: Per-stock analysis SHALL be thread-safe and isolated
Each stock SHALL be analyzed in its own thread via `ThreadPoolExecutor` (default `max_workers=3`). A single stock's failure SHALL NOT block or affect other stocks. Each analysis thread SHALL have its own data fetching, analysis, and result storage lifecycle.

#### Scenario: One stock fails
- **WHEN** stock A fails during data fetching but stock B succeeds
- **THEN** stock B's analysis SHALL complete normally, the pipeline SHALL record the error for stock A and continue processing remaining stocks

### Requirement: The pipeline SHALL support two analysis paths — traditional and agent
The system SHALL provide two analysis paths selected at runtime via `AGENT_MODE` config (boolean). The traditional path (`AGENT_MODE=false`, default): injects all strategy prompts and pre-computed context into a single LLM call via `GeminiAnalyzer.analyze()`. The agent path (`AGENT_MODE=true`): runs a ReAct loop with tool calling via `AgentExecutor` (when `AGENT_ARCH=single`) or a staged multi-agent pipeline (when `AGENT_ARCH=multi`). The `AGENT_ARCH` config controls the sub-architecture within the agent path only.

#### Scenario: Traditional analysis path
- **WHEN** `AGENT_MODE=false` (default)
- **THEN** the pipeline SHALL call `analyze_stock()` which builds an enhanced context dict and passes it to `GeminiAnalyzer.analyze()` for a single LLM call

#### Scenario: Agent analysis path
- **WHEN** `AGENT_MODE=true`
- **THEN** the pipeline SHALL call `_analyze_with_agent()` which instantiates `AgentExecutor` (when `AGENT_ARCH=single`) or `AgentOrchestrator` (when `AGENT_ARCH=multi`) for a multi-step ReAct loop with tool calling

### Requirement: The pipeline SHALL enhance context with all pre-computed data
The `_enhance_context()` method SHALL merge: realtime quote fields, chip distribution data, trend analysis results (MA alignment, MACD, RSI, bias), factor profile, regime prompt, allocation prompt, position sizing prompt, fundamental context, and sector/concept membership into a single enhanced context dictionary before LLM inference.

#### Scenario: Context injection
- **WHEN** the LLM is invoked for a stock analysis
- **THEN** its prompt SHALL include factor scores, market regime state, ATR-based position size, and fundamental context alongside the raw price data

### Requirement: The pipeline SHALL apply decision stability protection
After LLM inference, the pipeline SHALL run `stabilize_decision_with_structure()` which checks the LLM's decision against technical structure (support/resistance, capital flow). If the LLM recommends "buy" at a resistance level with negative capital flow, the decision SHALL be downgraded to "hold". If it recommends "sell" at support with positive capital flow, SHALL be downgraded to "hold".

#### Scenario: Buy signal at resistance
- **WHEN** the LLM outputs a "buy" recommendation but the price is at a resistance level with negative capital flow
- **THEN** the decision SHALL be downgraded to "hold, wait and see"

### Requirement: Notification SHALL be sent after all stocks complete
The pipeline SHALL collect all analysis results and send a single aggregate notification (or per-stock if `--single-notify` is set). Notification SHALL include market review data if available. The pipeline SHALL also save a local report file and optionally generate a Feishu document.

#### Scenario: Aggregate notification
- **WHEN** all stocks in the watchlist have been analyzed
- **THEN** the pipeline SHALL call `_send_notifications()` with the complete result list, generating a single aggregate report
