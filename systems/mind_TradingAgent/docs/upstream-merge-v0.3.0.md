# Upstream Merge: TauricResearch/TradingAgents v0.3.0

> **日期**: 2026-06-23
> **基线**: v0.2.5 → v0.3.0
> **合并提交**: `8a03f3f`

---

## 合并概况

| 项目 | 值 |
|---|---|
| 上游仓库 | `TauricResearch/TradingAgents` |
| 上游提交数 | 53 commits |
| 合并前 | v0.2.5 (`61522e1`) |
| 合并后 | v0.3.0 (`8a03f3f`) |
| 冲突文件数 | 31 (全部解决) |
| 新增文件 | ~30+ |

### 提交历史

```
640ae70 — chore(fork): rename tradingagents -> mind_tradingagent and apply customizations
8a03f3f — Merge upstream v0.3.0 into private fork
```

---

## 上游 v0.3.0 新特性

### LLM Providers

- **Amazon Bedrock** 作为一等提供商 (`pip install ".[bedrock]"`)
- **NVIDIA NIM**, **Kimi**, **Groq**, **Mistral** 提供商支持
- **OpenRouter** 支持
- **OpenAI-Compatible** 统一注册表 + 通用端点 (vLLM, LM Studio, llama.cpp)
- 废弃模型清理，thinking 配置简化
- 模型目录刷新到当前提供商 lineup

### 新数据源

- **FRED 宏观指标** (美联储数据: 利率, 通胀, 就业, GDP) — 可选提供商, 免费 API Key
- **Polymarket 预测市场** — 无需 API Key
- 可选数据源故障时优雅降级

### 结构化输出

- **Sentiment Analyst**: 使用 SentimentReport schema 的结构化输出 + free-text fallback
- **Research Manager / Trader / Portfolio Manager**: 统一结构化输出模式 (`bind_structured` + `invoke_structured_or_freetext`)
- `build_instrument_context` → `get_instrument_context_from_state` 重构

### 市场数据改进

- **Verified Market Snapshot**: 分析师精确数值声明的确定性真实数据快照
- **Ticker 身份解析**: `resolve_instrument_identity` 防止跨不同代码的公司幻觉
- **yfinance 过期 OHLCV 拒绝**: 拒绝并报告过期价格而非报错错误价格
- 商品/外汇/加密货币 ticker 支持 (e.g., `BTC-USD`)
- 中国 A 股基准: 上海 `.SS`, 深圳 `.SZ`

### Reddit 数据源

- **RSS-first 架构**: 默认使用 Atom/RSS feed (JSON 端点稳定返回 403)
- **429 退避**: 尊重 `Retry-After` 头
- 优雅降级: 始终返回占位符而非抛出异常

### 配置与 CLI

- **Temperature 控制**: `TRADINGAGENTS_TEMPERATURE` 环境变量 / `temperature` 配置项
- **Reasoning/Thinking 旋钮**: `openai_reasoning_effort`, `google_thinking_level`, `anthropic_effort`
- CLI 环境变量设置时跳过交互选择
- 统一 ticker 处理与符号标准化

### 工程改进

- CI 工作流: test/lint/smoke
- 全仓库 ruff 清理 (`ruff select --strict`)
- `VendorError` 统一异常层级
- Report-tree writer 在 CLI 和 API 之间共享
- 删除未使用的 `uv.lock` 和 `analyst_concurrency_limit` 配置项
- 推荐 Python 3.12

---

## 合并冲突解决

### 冲突分类

| 类型 | 文件数 | 解决策略 |
|---|---|---|
| 纯 import 路径冲突 | 18 | 接受 OURS (保留 `mind_tradingagent.` 路径) |
| import + 逻辑冲突 | 11 | 保留 OURS 路径 + 合并上游逻辑改进 |
| 纯内容冲突 | 2 (`.env.example`, `README.md`) | 手动合并中英文内容 |

### 关键冲突文件

| 文件 | 冲突说明 | 解决方式 |
|---|---|---|
| `trading_graph.py` | import 结构重组 + 新函数导入 | 合并双方 import 集: 保留 OURS 的 state 导入 + 上游的新工具函数 |
| `propagation.py` | typing 简化 + import 路径 | 保留 OURS 的完整 typing + `AgentState` 导入 |
| `setup.py` | 通配符 import vs 显式导入 | 采用上游的显式导入模式 + `mind_tradingagent.` 路径 |
| `agent_utils.py` | 按模块拆分 import vs 紧凑导入 | 保留 OURS 的按模块风格 + 上游的新工具模块 |
| `sentiment_analyst.py` | 完全重设计 (结构化输出 + 预取数据源) | 采用上游设计 + `mind_tradingagent.` 路径 |
| `default_config.py` | 环境变量前缀变更 | 保留 `MIND_TRADINGAGENT_*` 前缀 + 上游新增变量 |
| `reddit.py` | RSS-first 重设计 | 采用上游设计 + 保留 OURS 的 User-Agent 字符串 |
| `README.md` | 中英文混排 | 保留 OURS 的中文结构 + 嵌入上游英文内容 |

### 全局修复

- 所有 `from tradingagents.` → `from mind_tradingagent.`
- 所有 `import tradingagents.` → `import mind_tradingagent.`
- 字符串中的 `tradingagents` 引用 (patch targets, error messages, dynamic imports) 全部修复

---

## 新增/删除文件

### 新增上游文件 (迁移到 mind_tradingagent/)

```
mind_tradingagent/reporting.py
mind_tradingagent/llm_clients/bedrock_client.py
mind_tradingagent/agents/utils/macro_data_tools.py
mind_tradingagent/agents/utils/market_data_validation_tools.py
mind_tradingagent/agents/utils/prediction_markets_tools.py
mind_tradingagent/dataflows/errors.py
mind_tradingagent/dataflows/fred.py
mind_tradingagent/dataflows/market_data_validator.py
mind_tradingagent/dataflows/polymarket.py
mind_tradingagent/dataflows/symbol_utils.py
```

### 新增上游测试文件

```
tests/test_alpha_vantage_hardening.py
tests/test_bedrock_provider.py
tests/test_cli_config_precedence.py
tests/test_cli_env_skip.py
tests/test_cli_symbol_handling.py
tests/test_date_boundaries.py
tests/test_fred.py
tests/test_google_thinking_level.py
tests/test_i18n_coverage.py
tests/test_instrument_identity.py
tests/test_market_data_validator.py
tests/test_market_toolnode.py
tests/test_news_lookahead.py
tests/test_no_data_handling.py
tests/test_openai_compatible_provider.py
tests/test_openai_reasoning_effort.py
tests/test_openai_responses_base_url.py
tests/test_openrouter_model_select.py
tests/test_polymarket.py
tests/test_provider_registry.py
tests/test_reddit_fallback.py
tests/test_reporting.py
tests/test_stockstats_date_column.py
tests/test_stocktwits_resilience.py
tests/test_symbol_normalization_paths.py
tests/test_symbol_utils.py
tests/test_temperature_config.py
tests/test_vendor_errors.py
tests/test_vendor_routing.py
tests/test_yfinance_stale_ohlcv_guard.py
```

### 删除文件

- `mind_tradingagent/llm_clients/TODO.md` (上游已删除)
- `uv.lock` (上游已删除)

---

## 环境变量变更

旧前缀 (`TRADINGAGENTS_*`) 已替换为 `MIND_TRADINGAGENT_*`:

| 新环境变量 | 对应配置项 |
|---|---|
| `MIND_TRADINGAGENT_LLM_PROVIDER` | `llm_provider` |
| `MIND_TRADINGAGENT_DEEP_THINK_LLM` | `deep_think_llm` |
| `MIND_TRADINGAGENT_QUICK_THINK_LLM` | `quick_think_llm` |
| `MIND_TRADINGAGENT_LLM_BACKEND_URL` | `backend_url` |
| `MIND_TRADINGAGENT_OUTPUT_LANGUAGE` | `output_language` |
| `MIND_TRADINGAGENT_MAX_DEBATE_ROUNDS` | `max_debate_rounds` |
| `MIND_TRADINGAGENT_MAX_RISK_ROUNDS` | `max_risk_discuss_rounds` |
| `MIND_TRADINGAGENT_CHECKPOINT_ENABLED` | `checkpoint_enabled` |
| `MIND_TRADINGAGENT_BENCHMARK_TICKER` | `benchmark_ticker` |
| `MIND_TRADINGAGENT_TEMPERATURE` | `temperature` |
| `MIND_TRADINGAGENT_OPENAI_REASONING_EFFORT` | `openai_reasoning_effort` |
| `MIND_TRADINGAGENT_GOOGLE_THINKING_LEVEL` | `google_thinking_level` |
| `MIND_TRADINGAGENT_ANTHROPIC_EFFORT` | `anthropic_effort` |

新增 API Key 环境变量: `FRED_API_KEY`, `MISTRAL_API_KEY`, `MOONSHOT_API_KEY`, `GROQ_API_KEY`, `NVIDIA_API_KEY`, `OPENAI_COMPATIBLE_API_KEY`, `AWS_DEFAULT_REGION`, `AWS_PROFILE`

---

## 后续检查清单

- [ ] 安装依赖: `pip install -e ".[bedrock]"` (如需 Bedrock)
- [ ] 验证 import: `python -c "from mind_tradingagent.graph.trading_graph import TradingAgentsGraph"`
- [ ] 运行测试: `python -m pytest tests/ -x -q` (关注新测试)
- [ ] 检查新增环境变量是否需要配置
- [ ] 推送合并到远程: `git push origin main`
