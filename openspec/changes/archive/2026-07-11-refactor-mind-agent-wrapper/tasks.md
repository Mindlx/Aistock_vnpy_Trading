## 1. 提取信号加载模块

- [x] 1.1 新建 `src/services/__init__.py`
- [x] 1.2 新建 `src/services/signal_loader.py`，将 `_load_ly_signals_for_at` 中的逻辑提取为 `SignalLoader` 类
- [x] 1.3 实现 `SignalLoader.load_ly_signal()`（UnifiedCache RF+LGB 信号读取）
- [x] 1.4 实现 `SignalLoader.load_ml_factor()`（stock_analysis.db ML 因子读取）
- [x] 1.5 实现 `SignalLoader.load_at_signal()`（从 `data/tradingagent/ta_signals_*.json` 读取）
- [x] 1.6 实现会话级 per-stock 缓存（避免同会话重复查）
- [x] 1.7 新增 `tests/test_signal_loader.py` 覆盖三种场景（有缓存/无缓存/异常）

## 2. 提取上下文准备模块

- [x] 2.1 新建 `src/services/context_preparer.py`，将 `_get_preloaded_context` 提取为 `ContextPreparer` 类
- [x] 2.2 实现 `ContextPreparer.prepare_market_context()`（OHLCV + 技术指标）
- [x] 2.3 实现 `ContextPreparer.prepare_news_context()`（新闻情报）
- [x] 2.4 实现 `ContextPreparer.prepare_sentiment_context()`（情绪数据）
- [x] 2.5 实现 `ContextPreparer.build_injection_payload()`（格式化 LLM 注入消息）
- [x] 2.6 确保各子方法独立容错（单源失败不影响其他）
- [x] 2.7 新增 `tests/test_context_preparer.py`

## 3. 提取技术分析模块

- [x] 3.1 新建 `src/services/stock_analyzer.py`，提取 `_technical_rating` 和 `_fallback_akshare`
- [x] 3.2 实现 `StockAnalyzer.technical_rating()`（价格历史 → Buy/Hold/Sell）
- [x] 3.3 实现 `StockAnalyzer.fallback_analysis()`（yfinance 降级分析）
- [x] 3.4 新增 `tests/test_stock_analyzer.py`

## 4. 简化 Facade

- [x] 4.1 将 `MindTradingAgentWrapper.analyze_single` 改为委托调用新模块
- [x] 4.2 将 `_injected_create` 闭包逻辑替换为 `ContextPreparer.build_injection_payload()`
- [x] 4.3 删除 `_load_ly_signals_for_at`、`_get_preloaded_context`、`_technical_rating`、`_fallback_akshare` 四个私有方法
- [x] 4.4 验证现有 `run_batch` / `run_batch_and_save` 接口行为不变

## 5. 回归验证

- [x] 5.1 修复 `test_capital_flow.py:112`（模块级 sys.exit → if __name__）和 `test_fusion_engine.py`（方法签名变更）。剩余 9 项失败均为 `test_fusion.py` 预存问题，非本次引入
- [ ] 5.2 手动验证 `run_batch(["601801"], "2026-06-01")` 结果与重构前一致（需真实数据源环境）
