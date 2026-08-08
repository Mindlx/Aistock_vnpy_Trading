# 问股 Agent 全流程分析

> 文档类型: 子系统说明 | 适用范围: MindLynx-Aistock 问股功能
> 版本: 2026-08-08 | 校准基线: HEAD a24010c

## 一、端到端流程节点图

```
前端 ChatPage.tsx
  └─ agentChatStore.ts: fetch POST /chat/stream (SSE)
       │  response.body.getReader() 手动解析
       │  强制校验 accepted 首事件 (无则 throw protocolError)
       ▼
后端 systems/MindLynx-Aistock/api/v1/endpoints/agent.py:373 agent_chat_stream
  ├─ 检查 config.is_agent_available()
  ├─ queue = asyncio.Queue; progress_callback → queue.put
  ├─ run_sync: AgentExecutor.chat() (线程池)
  └─ event_generator: yield accepted → queue 事件 → done/error (300s 超时)
       ▼
systems/MindLynx-Aistock/src/agent/executor.py:386 AgentExecutor.chat (parse_dashboard=False)
  ├─ CHAT_SYSTEM_PROMPT + language_section + skills_section
  ├─ conversation_manager.get_or_create (30min TTL)
  ├─ 注入历史上下文 [系统提供的历史分析上下文]
  └─ run_agent_loop (ReAct 循环)
       ▼
systems/MindLynx-Aistock/src/agent/runner.py:364 run_agent_loop (max_steps=10)

  ├─ 剩余时间检查 (timeout/budget_guard)
  ├─ LLMToolAdapter.call_with_tools → LLM
  ├─ tool_calls 分支: _execute_tools (单工具内联/多工具并行≤5)
  │     non-retriable cache 去重 → tool_start/tool_done 事件
  └─ 无 tool_calls = final answer
       ▼
src/agent/tools/ (factory.get_tool_registry 统一注册)
  ├─ data: get_realtime_quote/get_daily_history/get_chip_distribution/get_stock_info/get_capital_flow/get_portfolio_snapshot
  ├─ analysis: analyze_trend/get_analysis_context...
  ├─ search: search_stock_news/search_comprehensive_intel
  ├─ market + backtest
       ▼
数据源 (多源降级链)
  ├─ get_realtime_quote → DataFetcherManager → CompositeDataPort
  ├─ get_daily_history → load_history_df (DB 缓存→fetch 链)
  └─ search_stock_news → intel.db 缓存 → SearchService
       ▼
LLM: LLMToolAdapter.call_completion 多模型降级 (LITELLM_MODEL/LLM_CHANNELS)
       ▼
输出: RunLoopResult → SSE done 事件 → 前端 thinkingSteps + finalContent
```

## 二、各节点数据调用

| 节点 | 文件:行 | 数据/调用 |
|------|---------|-----------|
| SSE 流式入口 | systems/MindLynx-Aistock/api/v1/endpoints/agent.py:373 | `agent_chat_stream`, queue + progress_callback |
| accepted 首事件 | systems/MindLynx-Aistock/api/v1/endpoints/agent.py:436 `event_generator` | 先 yield accepted → 循环 queue.get() 300s 超时 |
| 执行器构建 | systems/MindLynx-Aistock/api/v1/endpoints/agent.py:270 `_build_executor` | `build_agent_executor(config, skills)` |
| AgentExecutor.chat | systems/MindLynx-Aistock/src/agent/executor.py:386 | CHAT_SYSTEM_PROMPT, parse_dashboard=False |
| 历史会话 | systems/MindLynx-Aistock/src/agent/conversation.py ConversationManager | TTL 30min, get_or_create/add_message/get_history |
| ReAct 循环 | systems/MindLynx-Aistock/src/agent/runner.py:364 `run_agent_loop` | max_steps=10, budget_guard (剩余<8s 提前中止) |
| 工具执行 | systems/MindLynx-Aistock/src/agent/runner.py:622 `_execute_tools` | 单工具内联/多工具 ThreadPoolExecutor≤5 |
| 工具去重 | systems/MindLynx-Aistock/src/agent/runner.py `non-retriable cache` | 不可重试工具结果缓存，防 LLM 循环调用 |
| 统一注册 | systems/MindLynx-Aistock/src/agent/factory.py:165 `get_tool_registry` | ALL_*_TOOLS 五类 (data/analysis/search/market/backtest) |
| 实时行情 | systems/MindLynx-Aistock/src/agent/tools/data_tools.py:234 | `_get_fetcher_manager()` → CompositeDataPort 降级链 |
| 历史K线 | systems/MindLynx-Aistock/src/agent/tools/data_tools.py:296 | `load_history_df` (DB缓存→DataFetcherManager) |
| 情报搜索 | systems/MindLynx-Aistock/src/agent/tools/search_tools.py:72 | intel.db 缓存优先 → SearchService |
| LLM 调用 | systems/MindLynx-Aistock/src/agent/llm_adapter.py:304 `call_completion` | 多模型降级链 (LITELLM_MODEL/LLM_CHANNELS) |

## 三、Prompt 工程关键点

| 项 | 位置 | 内容 |
|----|------|------|
| **CHAT_SYSTEM_PROMPT** | systems/MindLynx-Aistock/src/agent/executor.py:171 | "你是一位{market_role}投资分析 Agent…{market_guidelines}" |
| **4阶段工作流** | systems/MindLynx-Aistock/src/agent/executor.py:176 | ①行情K线→②技术筹码→③情报→④综合分析，**禁止合并阶段** |
| **注入点** | systems/MindLynx-Aistock/src/agent/executor.py | `{default_skill_policy_section}` `{skills_section}` `{language_section}` |
| **语言控制** | systems/MindLynx-Aistock/src/agent/executor.py:213 | chat 模式 en→English/zh→中文 |
| **技能注入** | systems/MindLynx-Aistock/src/agent/factory.py resolve_skill_prompt_state | 激活 skills + skill_instructions |
| **工具 schema** | registry.py to_openai_tool | 每个工具 → OpenAI function JSON Schema |
| **历史上下文注入** | systems/MindLynx-Aistock/src/agent/executor.py chat() | [系统提供的历史分析上下文] user 消息 + assistant 占位 |

## 四、发现的潜在优化点

1. **LLM 配置依赖**: `LITELLM_MODEL`/`LLM_CHANNELS` 缺失时 LLM 层直接报 "No LLM configured" — 配置依赖，需确认环境已配。

2. **CHAT_SYSTEM_PROMPT 无 legacy 分离**: systems/MindLynx-Aistock/src/agent/executor.py:208 `LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT = CHAT_SYSTEM_PROMPT` 是别名，问股**没有** dashboard 那样的新旧 prompt 切换机制 — 若需 A/B 测试 prompt 需扩展。

3. **多工具并行 ≤5** + **non-retriable cache**: 防止 LLM 循环调用同一工具 — 良好防抖设计，无缺陷。

4. **SSE 300s 超时** + **前端取消 (serverCancellation)**: 前后端超时/取消协议完整，无缺口。

## 五、结论

问股全流程架构健康 — SSE 协议前后端严格匹配（accepted 强制校验）、数据全部经 CompositeDataPort 降级链、prompt 结构清晰。**无 CRITICAL bug。** 潜在改进点集中在 prompt 版本管理（chat 无 legacy 切换）与 LLM 配置依赖。
