## Context

AlphaSift 是独立的 Python 包，通过 `pip install alphasift` 安装，提供选股引擎核心逻辑（策略执行、因子打分、LLM 排序、热点发现）。

我们需要做的是将其接入融合项目的 Web UI：

```
用户 → 前端选股页面 → API 路由 → AlphaSift 引擎层 → 我们的数据源(Tushare/Akshare)
```

## Decisions

1. **AlphaSift 作为核心引擎** — 不重复实现选股逻辑，直接调用 `alphasift.screen()` API
2. **API 路由** — 新建 `api/v1/endpoints/alphasift.py`，参考上游的 6 个端点
3. **前端页面** — 新增 `/screening` 选股页 + `/hotspots` 热点看板
4. **数据源** — 通过 AlphaSift 的 `SNAPSHOT_SOURCE_PRIORITY` 配置指向我们的 Tushare token

## Risks

- Tushare token 需要配置环境变量
- AlphaSift 的 LLM 排序依赖 LLM API 配置
