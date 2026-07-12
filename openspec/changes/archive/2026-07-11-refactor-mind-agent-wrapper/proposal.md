## Why

`src/mind_agent_wrapper.py` 中 `MindTradingAgentWrapper` 类（781 行）有 4 个函数圈复杂度超过 15，其中 `_load_ly_signals_for_at`（123 行/复杂度 33）和 `_get_preloaded_context`（203 行/复杂度 31）已接近不可维护状态。逻辑相互交织导致修改风险高、测试困难，亟需拆分为职责单一的内聚模块。

## What Changes

1. 将 `_load_ly_signals_for_at` 和 `_get_preloaded_context` 分别拆为独立 service 类
2. 将 `analyze_single` 和 `_technical_rating` 中的业务逻辑提取到新的 analyzer 模块
3. 保留 `MindTradingAgentWrapper` 作为 Facade 入口，内部委托给新模块
4. 为每个新模块编写单元测试

## Capabilities

### New Capabilities
- `signal-loading`: 信号加载服务，封装 `_load_ly_signals_for_at` 中的 LY/ML/AT 信号获取逻辑
- `context-preparation`: 上下文准备服务，封装 `_get_preloaded_context` 中的研报/新闻/看板数据加载
- `stock-analysis`: 个股分析服务，封装 `analyze_single` 和 `_technical_rating` 中的技术面评分逻辑

### Modified Capabilities
- 无（纯重构，不改接口行为）

## Impact

- `src/mind_agent_wrapper.py` — 减少约 500 行，保留 Facade 接口
- `src/signal_loader.py` — 新增（~120 行）
- `src/context_preparer.py` — 新增（~200 行）
- `src/stock_analyzer.py` — 新增（~150 行）
- `tests/test_signal_loader.py` — 新增
- `tests/test_context_preparer.py` — 新增
- `tests/test_stock_analyzer.py` — 新增
