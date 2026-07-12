## Why

AlphaSift 是一个独立的 AI 选股引擎（`pip install alphasift`），提供 10 个内置选股策略、全市场扫描、LLM 排序和热点发现。上游项目已集成完整的前端 + API。

但我们有更丰富的数据源（Tushare 付费会员、17 款数据链、数据湖），可以做出比上游更好的选股体验。

## What Changes

1. 安装 `alphasift` Python 包
2. 在融合项目中新增选股 API 端点（FastAPI router）
3. 新增前端选股页面（StockScreeningPage + 热点看板）
4. 复用我们已有的数据源（Tushare/Akshare/东财）作为 AlphaSift 的数据后端

## Capabilities

### New Capabilities
- `stock-screening`: 全市场选股（策略筛选 + LLM 排序 + 结果持久化）
- `hotspot-discovery`: 热点题材发现（板块热度排行 + 龙头股解析）
- `alpha-sift-api`: 选股 API 端点集

### Modified Capabilities
- 无（新功能模块）

## Impact

- `pip install alphasift` 新增依赖
- 新增 `api/v1/endpoints/alphasift.py` — 选股 API
- 新增前端页面（路由 /screening + /hotspots）
- 复用现有 `src/` 下的数据源基础设施
