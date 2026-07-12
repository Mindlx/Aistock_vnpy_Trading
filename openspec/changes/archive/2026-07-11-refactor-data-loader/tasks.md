## 1. 创建 loaders 包 + 拆分文件

- [x] 1.1 新建 `src/loaders/__init__.py`（重导出所有 Loader 类）
- [x] 1.2 从 `src/data_loader.py` 提取 LynxDataLoader → `src/loaders/lynx_loader.py`
- [x] 1.3 提取 MindLynxDataLoader → `src/loaders/mindlynx_loader.py`
- [x] 1.4 提取 TradingAgentDataLoader → `src/loaders/tradingagent_loader.py`
- [x] 1.5 提取 MLFactorLoader + Alpha158Loader → `src/loaders/各自文件`
- [x] 1.6 提取 UnifiedDataLoader → `src/loaders/unified_loader.py`

## 2. 兼容层

- [x] 2.1 将 `src/data_loader.py` 缩减为 `from src.loaders import *`
- [x] 2.2 验证 `from src.data_loader import MindLynxDataLoader` 仍可用

## 3. 验证

- [ ] 3.1 验证各 Loader 类可正常导入
- [ ] 3.2 提交并归档
