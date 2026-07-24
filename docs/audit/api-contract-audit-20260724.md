# API 契约审计报告

日期: 2026-07-24
前端: aa68d45 (ZhuLinsen 原始上游)
后端: systems/MindLynx-Aistock (独立运行，无 PYTHONPATH)

## 统计
前端调用总数: 83
后端路由总数: 99

## 逐端对照

| 方法 | 路径 | 后端 | 状态 | 说明 |
|------|------|:----:|:----:|------|
| GET | `/api/v1/agent/chat` | [POST] | ✅ 真实 | |
| GET | `/api/v1/agent/skills` | [GET] | ✅ 真实 | |
| GET | `/api/v1/agent/status` | [GET] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| GET | `/api/v1/agent/chat/sessions` | [GET] | ✅ 真实 | |
| POST | `/api/v1/agent/chat/send` | [POST] | ✅ 真实 | |
| DELETE | `/api/v1/agent/chat/sessions/{id}` | — | ❌ 缺失 | |
| GET | `/api/v1/alerts/rules` | [POST,GET] | ✅ 真实 | |
| POST | `/api/v1/alerts/rules` | [POST,GET] | ✅ 真实 | |
| GET | `/api/v1/alerts/triggers` | [GET] | ✅ 真实 | |
| GET | `/api/v1/alerts/notifications` | [GET] | ✅ 真实 | |
| GET | `/api/v1/alphasift/status` | [GET] | ✅ 真实 | |
| POST | `/api/v1/alphasift/screen` | [POST] | ✅ 真实 | |
| POST | `/api/v1/alphasift/screen/tasks` | [POST] | ✅ 真实 | |
| GET | `/api/v1/alphasift/screen/tasks/{id}` | — | ❌ 缺失 | |
| GET | `/api/v1/alphasift/strategies` | [GET] | ✅ 真实 | |
| GET | `/api/v1/alphasift/hotspots` | [GET] | ✅ 真实 | |
| POST | `/api/v1/alphasift/install` | [POST] | ✅ 真实 | |
| POST | `/api/v1/analysis/analyze` | [POST] | ✅ 真实 | |
| POST | `/api/v1/analysis/market-review` | [POST] | ✅ 真实 | |
| GET | `/api/v1/analysis/tasks` | [GET] | ✅ 真实 | |
| GET | `/api/v1/analysis/tasks/stream` | [GET] | ✅ 真实 | |
| GET | `/api/v1/auth/status` | [GET] | ✅ 真实 | |
| POST | `/api/v1/auth/settings` | [POST] | ✅ 真实 | |
| POST | `/api/v1/auth/login` | [POST] | ✅ 真实 | |
| POST | `/api/v1/auth/change-password` | [POST] | ✅ 真实 | |
| POST | `/api/v1/auth/logout` | [POST] | ✅ 真实 | |
| POST | `/api/v1/backtest/run` | [POST] | ✅ 真实 | |
| GET | `/api/v1/backtest/results` | [GET] | ✅ 真实 | |
| GET | `/api/v1/backtest/performance` | [GET] | ✅ 真实 | |
| GET | `/api/v1/decision-signals` | [GET] | ✅ 真实 | |
| GET | `/api/v1/decision-signals/outcomes/stats` | [GET] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| POST | `/api/v1/decision-signals/reassess` | — | ❌ 缺失 | |
| POST | `/api/v1/decision-signals/outcomes/run` | — | ❌ 缺失 | |
| GET | `/api/v1/decision-signals/outcomes` | [GET] | ✅ 真实 | |
| GET | `/api/v1/history` | [GET,DELETE] | ✅ 真实 | |
| DELETE | `/api/v1/history` | [GET,DELETE] | ✅ 真实 | |
| GET | `/api/v1/history/{id}` | [DELETE] | ✅ 真实 | |
| GET | `/api/v1/history/{id}/news` | — | ❌ 缺失 | |
| GET | `/api/v1/history/{id}/markdown` | — | ❌ 缺失 | |
| GET | `/api/v1/history/{id}/diagnostics` | — | ❌ 缺失 | |
| GET | `/api/v1/history/{id}/flow` | — | ❌ 缺失 | |
| DELETE | `/api/v1/history/by-code/{code}` | — | ❌ 缺失 | |
| GET | `/api/v1/history/stocks` | [GET] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| GET | `/api/v1/portfolio/accounts` | [POST,GET] | ✅ 真实 | |
| POST | `/api/v1/portfolio/accounts` | [POST,GET] | ✅ 真实 | |
| GET | `/api/v1/portfolio/snapshot` | [GET] | ✅ 真实 | |
| GET | `/api/v1/portfolio/risk` | [GET] | ✅ 真实 | |
| POST | `/api/v1/portfolio/fx/refresh` | [POST] | ✅ 真实 | |
| POST | `/api/v1/portfolio/trades` | [POST,GET] | ✅ 真实 | |
| GET | `/api/v1/portfolio/trades` | [POST,GET] | ✅ 真实 | |
| POST | `/api/v1/portfolio/cash-ledger` | [POST,GET] | ✅ 真实 | |
| GET | `/api/v1/portfolio/cash-ledger` | [POST,GET] | ✅ 真实 | |
| POST | `/api/v1/portfolio/corporate-actions` | [POST,GET] | ✅ 真实 | |
| GET | `/api/v1/portfolio/corporate-actions` | [POST,GET] | ✅ 真实 | |
| GET | `/api/v1/portfolio/imports/csv/brokers` | [GET] | ✅ 真实 | |
| POST | `/api/v1/portfolio/imports/csv/parse` | [POST] | ✅ 真实 | |
| POST | `/api/v1/portfolio/imports/csv/commit` | [POST] | ✅ 真实 | |
| POST | `/api/v1/stocks/extract-from-image` | [POST] | ✅ 真实 | |
| POST | `/api/v1/stocks/parse-import` | [POST] | ✅ 真实 | |
| GET | `/api/v1/stocks/{code}/quote` | — | ❌ 缺失 | |
| GET | `/api/v1/stocks/{code}/history` | — | ❌ 缺失 | |
| GET | `/api/v1/system/config` | [GET,PUT] | ✅ 真实 | |
| GET | `/api/v1/system/config/export` | [GET] | ✅ 真实 | |
| GET | `/api/v1/system/config/schema` | [GET] | ✅ 真实 | |
| GET | `/api/v1/system/config/setup/status` | [GET] | ✅ 真实 | |
| GET | `/api/v1/system/config/generation-backends/status` | [GET] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| POST | `/api/v1/system/config/generation-backends/status/preview` | [POST] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| POST | `/api/v1/system/config/generation-backends/smoke-test` | [POST] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| GET | `/api/v1/system/config/agent-backends/status` | [GET] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| POST | `/api/v1/system/config/agent-backends/status/preview` | [POST] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| GET | `/api/v1/system/scheduler/status` | [GET] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| POST | `/api/v1/system/scheduler/run-now` | [POST] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| POST | `/api/v1/system/config/validate` | [POST] | ✅ 真实 | |
| POST | `/api/v1/system/config/import` | [POST] | ✅ 真实 | |
| POST | `/api/v1/system/config/llm/test-channel` | [POST] | ✅ 真实 | |
| POST | `/api/v1/system/config/notification/test-channel` | [POST] | ✅ 真实 | |
| POST | `/api/v1/system/config/llm/discover-models` | [POST] | ✅ 真实 | |
| PUT | `/api/v1/system/config` | [GET,PUT] | ✅ 真实 | |
| GET | `/api/v1/stocks/watchlist` | [GET] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| POST | `/api/v1/stocks/watchlist/add` | [POST] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| POST | `/api/v1/stocks/watchlist/remove` | [POST] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| GET | `/api/v1/usage/dashboard` | [GET] | ⚠️ 桩 | 返回默认值，无业务逻辑 |
| GET | `/api/v1/usage/summary` | [GET] | ✅ 真实 | |

## 汇总

| 分类 | 数量 |
|------|:----:|
| ✅ 真实实现 | 58 |
| ⚠️ 桩 | 14 |
| ❌ 缺失 | 11 |
| **合计** | **83** |
