## Why

`src/data_loader.py` 中堆积了 6 个 DataLoader 类（共 939 行），每个类独立负责一种数据源的加载逻辑。多个 DataLoader 放在一个文件导致 import 时加载所有依赖、修改任一 Loader 需跨大量上下文、新增 Loader 容易与现有逻辑纠缠。

## What Changes

1. 将 6 个 DataLoader 类拆分为独立的文件，按数据源分目录
2. 保留 `UnifiedDataLoader` 作为统一入口 Facade
3. 每个 DataLoader 保持现有接口不变（`load_by_date` / `load_all` / `load_by_stock_and_date`）

## Capabilities

### New Capabilities
- `lynx-data-loader`: Lynx 信号加载器（RF 量化信号）
- `mindlynx-data-loader`: MindLynx 分析报告加载器
- `tradingagent-data-loader`: TradingAgent 日志解析器
- `ml-factor-loader`: ML 因子信号加载器
- `alpha158-loader`: Alpha158 因子信号加载器

### Modified Capabilities
- 无（纯重构，不改接口）

## Impact

- `src/data_loader.py` — 减少约 900 行，保留 `UnifiedDataLoader` 作为 Facade + 兼容 import
- `src/loaders/__init__.py` — 新增
- `src/loaders/lynx_loader.py` — 新增
- `src/loaders/mindlynx_loader.py` — 新增
- `src/loaders/tradingagent_loader.py` — 新增
- `src/loaders/ml_factor_loader.py` — 新增（含 Alpha158Loader）
- 所有现有 import 路径保持兼容
