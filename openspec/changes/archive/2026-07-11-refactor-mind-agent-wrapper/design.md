## Context

`MindTradingAgentWrapper`（781 行，`src/mind_agent_wrapper.py`）是一个封装了 mind_TradingAgent 调用的批量分析类。目前将所有逻辑塞在一个类中：信号加载（LY/ML）、上下文准备（研报/新闻/看板）、技术面评分、fallback 降级等，函数间共享 `self` 状态，耦合度高。

圈复杂度数据：
- `_load_ly_signals_for_at`: 33（123 行）
- `_get_preloaded_context`: 31（203 行）
- `_technical_rating`: 17（90 行）
- `analyze_single`: 15（131 行）

## Goals / Non-Goals

**Goals:**
- 将信号加载、上下文准备、技术分析拆为独立的 service 类
- 每个新模块圈复杂度 ≤ 10，单文件 ≤ 200 行
- `MindTradingAgentWrapper` 保留为 Facade，对外接口不变
- 为每个新模块编写单元测试

**Non-Goals:**
- 不改动 mind_TradingAgent 代码
- 不改动数据结构、配置格式
- 不引入新依赖

## Decisions

1. **信号加载 → `services/signal_loader.py`**
   - 提取 `_load_ly_signals_for_at` 中 LY/ML/AT 信号从 UnifiedCache 和 stock_analysis.db 的读取逻辑
   - 封装为 `SignalLoader` 类，方法：`load_ly_signal`, `load_ml_factor`, `load_at_signal`
   - 缓存：同一只股票在同一会话中只查一次

2. **上下文准备 → `services/context_preparer.py`**
   - 提取 `_get_preloaded_context` 中研报、新闻、看板数据、行情的多源拼装逻辑
   - 封装为 `ContextPreparer` 类，方法：`prepare_market_context`, `prepare_news_context`, `prepare_sentiment_context`
   - 数据源：UnifiedCache / WarehouseReader / akshare

3. **技术分析 → `services/stock_analyzer.py`**
   - 提取 `_technical_rating` 和 `_fallback_akshare` 中的技术面判断逻辑
   - 封装为 `StockAnalyzer` 类，方法：`technical_rating`, `fallback_analysis`
   - 注入数据提供者（yfinance/akshare）避免硬依赖

4. **Facade 简化**
   - `MindTradingAgentWrapper.analyze_single` 改为委托调用：`SignalLoader` → `ContextPreparer` → LLM 辩论 → `StockAnalyzer.fallback`
   - 原 `_injected_create` 闭包逻辑改为 `ContextPreparer.build_injection_payload`

## Risks / Trade-offs

- [风险] 新模块初始化引入额外 import 链 → 惰性加载，仅在使用时导入
- [风险] 拆模块后代码行数总量增加（接口胶水代码） → 可接受，可维护性提升为主
- [风险] _injected_create 闭包逻辑涉及 monkey-patch → 封装到 ContextPreparer 的方法中，与 Wrapper 解耦
