## Context

`src/data_loader.py` 含 6 个类共 939 行：LynxDataLoader(225行)、MindLynxDataLoader(248行)、TradingAgentDataLoader(248行)、MLFactorLoader(~50行)、Alpha158Loader(~50行)、UnifiedDataLoader(118行)。

## Goals / Non-Goals

**Goals:**
- 每个 Loader 拆为独立文件，`src/loaders/` 包下
- `UnifiedDataLoader` 保持为 Facade，内部 import 新模块
- 兼容现有 `from src.data_loader import Xxx` 路径

**Non-Goals:**
- 不改动数据加载逻辑
- 不改动返回数据结构

## Decisions

1. **文件结构**: `src/loaders/{lynx,mindlynx,tradingagent,ml_factor,alpha158}_loader.py + __init__.py`
2. **兼容层**: `src/data_loader.py` 保留 import 重导出 `from src.loaders import *`，现有 import 无需改动
3. **拆分粒度**: MLFactorLoader 和 Alpha158Loader（各 50 行）可放入同一文件

## Risks / Trade-offs

- 无风险 — 纯文件拆分 + import 重导出
