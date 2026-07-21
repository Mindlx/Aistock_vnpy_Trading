# tradingagent-data-loader Specification

## Purpose
TradingAgentDataLoader 从 mind_TradingAgent 子系统加载多智能体辩论结果。解析 `data/tradingagent/ta_signals_*.json` 文件，提取每只股票的评级（StrongBuy/Buy/Hold/Sell/StrongSell）和辩论状态。支持批量加载所有股票和按股票代码查询。

## Requirements
### Requirement: TradingAgentDataLoader parses debate logs

The system SHALL provide a `TradingAgentDataLoader` class that parses TradingAgent debate state logs.

#### Scenario: Load all stocks by date
- **WHEN** `TradingAgentDataLoader().load_all_by_date("2026-07-01")` is called and JSON file exists
- **THEN** it returns a dict of `{stock_code: {rating, debate_state, final_decision, ...}}`

#### Scenario: JSON file missing
- **WHEN** the TA signal JSON file does not exist for the date
- **THEN** it returns an empty dict

#### Scenario: Load single stock
- **WHEN** `TradingAgentDataLoader().load_by_stock_and_date("601801", "2026-07-01")` is called
- **THEN** it returns parsed debate state for that stock, including rating and final_decision
