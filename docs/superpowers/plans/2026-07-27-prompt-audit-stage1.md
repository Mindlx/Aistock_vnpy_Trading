# ML 子系统 Prompt 全链条审计 — Stage 1 报告

> **课题**: Prompt 全链条审计  
> **范围**: systems/MindLynx-Aistock 全部 26 个 LLM prompt  
> **状态**: Stage 1 完成（流向地图 + 问题识别）  
> **日期**: 2026-07-27  
> **下一步**: Stage 2 — 逐条深入分析与优化方案设计

---

## 目录

1. [方法论与范围](#1-方法论与范围)
2. [完整 Prompt 清单](#2-完整-prompt-清单)
3. [触发路径与流向地图](#3-触发路径与流向地图)
4. [占位符注入关系](#4-占位符注入关系)
5. [发现的 5 个问题](#5-发现的-5-个问题)
6. [附录：各 Prompt 完整源码位置](#6-附录各-prompt-完整源码位置)

---

## 1. 方法论与范围

### 1.1 搜索方法

- 在 `systems/MindLynx-Aistock/src/` 下递归搜索所有 `"""` 三引号字符串
- 排除: 模块/函数/类 docstring、注释、test 文件
- 仅保留实际用作 LLM system prompt / user prompt / message 内容的字符串
- 补充: 动态生成的 prompt 函数（如 `build_user_prompt`、`_build_review_prompt`）

### 1.2 范围边界

| 包含 | 不包含 |
|:-----|:-------|
| ML 子系统 (MindLynx-Aistock) | TA 子系统 (mind_TradingAgent) |
| 系统 prompt (system role) | LY 子系统 (lynx_vnpy) |
| 用户 prompt (user message) | vnpy 框架自身的 prompt |
| 动态 prompt 生成函数 | 前端 JavaScript/TypeScript |
| 工具描述 (tool descriptions) | API 响应格式化模板 |
| 评分标准 | 数据库查询语句 |

### 1.3 度量指标

- **字符数**: 三引号字符串的原始字符长度（含换行和缩进）
- **定位**: 文件路径:行号
- **注入方式**: 静态拼接 / 占位符替换 / 动态函数生成
- **触发频率**: 每次请求 / 定时触发 / 按需

---

## 2. 完整 Prompt 清单

### 2.1 核心 System Prompt（5 个）

| # | 名称 | 文件 | 行 | 字符 | 用途 |
|:-:|:----|:----|:-:|:---:|:----|
| P1 | `AGENT_SYSTEM_PROMPT` | `src/agent/executor.py` | 56 | 3,247 | 整点分析 Agent 模式、问股 Agent 模式 |
| P2 | `LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT` | `src/agent/executor.py` | 169 | 3,247 | 旧版兼容 → 现= P1 别名 |
| P3 | `CHAT_SYSTEM_PROMPT` | `src/agent/executor.py` | 171 | 856 | 问股 Chat 模式（自然语言输出） |
| P4 | `SYSTEM_PROMPT` | `src/analyzer.py` | 1599 | 3,712 | 整点分析 GeminiAnalyzer 模式 |
| P5 | `LEGACY_DEFAULT_SYSTEM_PROMPT` | `src/analyzer.py` | 1468 | 4,332 | 旧版 analyzer prompt（含 CORE_TRADING_SKILL_POLICY） |
| P6 | `TEXT_SYSTEM_PROMPT` | `src/analyzer.py` | 1729 | 104 | 纯文本回退 prompt |

#### P1: AGENT_SYSTEM_PROMPT (executor.py:56)

```
长度: 3,247 字符
结构:
  1. {market_role} + {market_guidelines}  — 市场角色与规则
  2. 4 阶段工作流程（行情→技术→情报→报告）
  3. 6 条规则
  4. {skills_section}  — 激活的交易技能
  5. 决策仪表盘 JSON 输出格式（完整 schema，约 70 个字段）
  6. {scoring_criteria}  — 评分标准 (80-100/60-79/40-59/0-39)
  7. 5 条核心原则
  8. {action_guardrails}  — 操作护栏
  9. {language_section}  — 语言指令
使用: AgentExecutor.run() 和 chat() 中通过 format() 注入占位符
```

#### P3: CHAT_SYSTEM_PROMPT (executor.py:171)

```
长度: 856 字符
与 P1 的区别:
  - 无结构化 JSON schema
  - 无硬性输出约束
  - 指令是"自然语言回答"而非"输出 JSON"
  - 同样引用 {scoring_criteria} 和 {action_guardrails}
使用: AgentExecutor.chat() 中 format() 注入
```

#### P4: SYSTEM_PROMPT (analyzer.py:1599)

```
长度: 3,712 字符
结构:
  1. {market_placeholder} — 市场角色
  2. 4 阶段工作流程（与 P1 相同）
  3. 6 条规则（与 P1 基本相同）
  4. 决策仪表盘 JSON 输出格式（与 P1 基本相同的 schema）
  5. {scoring_criteria}
  6. {action_guardrails}
  7. {language_section}
使用: GeminiAnalyzer._get_analysis_system_prompt() 中 replace() 注入
```

#### P5: LEGACY_DEFAULT_SYSTEM_PROMPT (analyzer.py:1468)

```
长度: 4,332 字符
与 P4 的区别:
  - 多一个 {default_skill_policy_section}(含 CORE_TRADING_SKILL_POLICY_ZH)
  - 其余部分与 P4 相同
使用: 同上，当 use_legacy_default_prompt=True 时
```

### 2.2 评分标准与护栏（4 个）

| # | 名称 | 文件 | 行 | 字符 | 用途 |
|:-:|:----|:----|:-:|:---:|:----|
| P7 | `SCORING_CRITERIA_A` | `src/core/prompt_config.py` | 26 | 645 | 完整评分标准（含校准注释） |
| P8 | `SCORING_CRITERIA_B` | `src/core/prompt_config.py` | 46 | 156 | 精简评分标准 |
| P9 | `ACTION_GUARDRAILS_A` | `src/core/prompt_config.py` | 60 | 260 | 操作护栏 |
| P10 | `CANONICAL_DECISION_SCALE_PROMPT_ZH` | `src/schemas/decision_scale.py` | 34 | 558 | 决策量表（评分↔动作映射） |

#### P7: SCORING_CRITERIA_A (prompt_config.py:26)

```
- 80-100: 强烈看多
- 60-79: 看多
- 40-59: 中性
- 0-39: 看空
- 52/49 阈值体系
- LLM 大数值幻觉防护
- 多技能冲突处理
总行数: ~14 行
校准状态: 有 @calibration 标记
```

#### P10: CANONICAL_DECISION_SCALE_PROMPT_ZH (decision_scale.py:34)

```
- 80-100: 强烈看多 → 买入/加仓
- 60-79: 看多 → 买入/持有
- 40-59: 中性 → 持有/观望
- 20-39: 看空 → 减仓/卖出
- 0-19: 强烈看空 → 卖出/清仓
- 评分低于 40 时 guardrail 降级买入建议
这是 P7 的"另一个版本"——映射关系不完全一致
```

### 2.3 多 Agent 专用 Prompt（6 个）

| # | 名称 | 文件 | 行 | 字符 | 用途 |
|:-:|:----|:----|:-:|:---:|:----|
| P11 | `TechnicalAgent.system_prompt()` | `src/agent/agents/technical_agent.py` | 43 | 905 | 技术分析 Agent |
| P12 | `IntelAgent.system_prompt()` | `src/agent/agents/intel_agent.py` | 32 | 2,001 | 情报 Agent |
| P13 | `RiskAgent.system_prompt()` | `src/agent/agents/risk_agent.py` | 36 | 1,619 | 风险排查 Agent |
| P14 | `PortfolioAgent.system_prompt()` | `src/agent/agents/portfolio_agent.py` | 55 | 311 | 组合分配 Agent |
| P15 | `SkillAgent.system_prompt()` | `src/agent/agents/skill_agent.py` | 53 | 604 | 技能评估 Agent |
| P16 | `DecisionAgent.system_prompt()` | `src/agent/agents/decision_agent.py` | 33/58 | 525/3,739 | 决策综合 Agent（chat/dashboard） |

**注意**: 这 6 个 prompt 仅在 `ARCH=multi` 配置下启用。当前默认是 `ARCH=single`，它们处于未使用状态。

### 2.4 技能策略 Prompt（2 个）

| # | 名称 | 文件 | 行 | 字符 | 用途 |
|:-:|:----|:----|:-:|:---:|:----|
| P17 | `CORE_TRADING_SKILL_POLICY_ZH` | `src/agent/skills/defaults.py` | 24 | 715 | 核心交易策略基线 |
| P18 | `TECHNICAL_SKILL_RULES_EN` | `src/agent/skills/defaults.py` | 62 | 421 | 技术策略规则（英文） |

#### P17: CORE_TRADING_SKILL_POLICY_ZH (defaults.py:24)

```
覆盖: 不追高规则、多头排列要求、筹码结构、狙击点偏好、风险检查
使用: 1) LEGACY_DEFAULT_SYSTEM_PROMPT 内联拼接
      2) AgentExecutor.default_skill_policy 注入
```

### 2.5 辅助 Prompt（4 个）

| # | 名称 | 文件 | 行 | 字符 | 用途 |
|:-:|:----|:----|:-:|:---:|:----|
| P19 | `_build_language_section()` | `executor.py` | 213 | ~250 | 输出语言指令（中/英） |
| P20 | `SIGNAL_CORRELATION_A` | `prompt_config.py` | 79 | 342 | 成交量信号多机制区分 |
| P21 | `EXTRACT_PROMPT` | `services/image_stock_extractor.py` | 35 | 714 | 图片提取股票代码 |
| P22 | `NL_PARSE_PROMPT` | `bot/dispatcher.py` | 371 | 1,284 | 自然语言意图路由 |
| P23 | `_TEXT_SMOKE_PROMPT` | `services/generation_backend_status_service.py` | 174 | 46 | 健康检查（文本） |
| P24 | `_JSON_SMOKE_PROMPT` | `services/generation_backend_status_service.py` | 175 | ~85 | 健康检查（JSON） |

### 2.6 市场时段 Prompt（1 组）

| # | 名称 | 文件 | 字符 | 用途 |
|:-:|:----|:----|:---:|:----|
| P25 | `_PHASE_PROMPTS` 字典 | `market_phase_prompt.py` | 6×~150 | 盘前/盘中/午休/尾盘/盘后/非交易的策略建议 |

### 2.7 动态 Prompt 生成函数（3 个）

| # | 名称 | 文件 | 约字符 | 用途 |
|:-:|:----|:----|:-----:|:----|
| P26 | `build_user_prompt()` | `analyzer.py:2654` | ~3,500 | 动态构建用户消息（含行情表、因子、新闻等） |
| P27 | `_build_review_prompt()` | `market_analyzer.py:1394` | ~2,500 | 动态构建大盘复盘 prompt |
| P28 | `_build_agent_message()` | `pipeline.py:1231` | ~500 | 构建 Agent 任务消息 |

---

## 3. 触发路径与流向地图

### 3.1 三条外部入口

```
┌─────────────────────────────────────────────────────────────────────┐
│                        3 条外部入口                                  │
│                                                                     │
│  Path A: 整点分析                          Path B: 问股              │
│  main.py --schedule                        POST /chat/stream        │
│  11:00 / 14:00                             WebUI 8000               │
│       │                                         │                   │
│       ▼                                         ▼                   │
│  StockAnalysisPipeline                    AgentExecutor              │
│  .run()                                   .chat()                   │
│       │                                         │                   │
│  ┌────┴────┐                                    │                   │
│  │         ▼                                    │                   │
│  │  GeminiAnalyzer  ← config 决定路径             │                   │
│  │  .analyze()                                   │                   │
│  │         │                    ┌────────────────┘                   │
│  │         ▼                    ▼                                    │
│  │  ┌──────────────────────────────────────┐                        │
│  │  │           LLM 调用                    │                        │
│  │  │  (litellm Router)                     │                        │
│  │  └──────────────────────────────────────┘                        │
│  │                                                                   │
│  └─ AgentExecutor ← 另一分支路径                                      │
│     .run()                                                           │
│                                                                     │
│  Path C: 大盘复盘                                                    │
│  main.py → MarketAnalyzer.run_daily_review()                        │
│  11:45 / 15:45                                                      │
│       │                                                             │
│       ▼                                                             │
│  MarketAnalyzer.generate_market_review()                            │
│       │                                                             │
│       ▼                                                             │
│  _build_review_prompt() → LLM (独立 prompt, 无标准块)                │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Path A 内部：两条子路径

#### 子路径 A1: GeminiAnalyzer 模式（默认）

```
StockAnalysisPipeline.run()
  └─ _precompute_analysis_inputs()      ← FactorEngine 14因子 + 横截面
       └─ process_single_stock()  [ThreadPoolExecutor]
            └─ analyze_stock(code)
                 └─ self.analyzer.analyze(enhanced_data)  ← GeminiAnalyzer
                      │
                      ├─ _get_analysis_system_prompt()
                      │    ├─ P4 (SYSTEM_PROMPT, 3712) 或 P5 (LEGACY, 4332)
                      │    ├─ + {scoring_criteria}   ← P7/P8 from prompt_config
                      │    └─ + {action_guardrails}  ← P9 from prompt_config
                      │
                      ├─ build_user_prompt()     ← P26 (~3500 字符动态)
                      │    ├─ 实时行情表
                      │    ├─ 技术指标表
                      │    ├─ 筹码分布表
                      │    ├─ 14 因子评分
                      │    ├─ 新闻情报
                      │    └─ 资金流向
                      │
                      └─ LLM.generate(system + user)
```

#### 子路径 A2: AgentExecutor 模式（当配置启用时）

```
StockAnalysisPipeline.run()
  └─ analyze_stock()
       └─ _analyze_with_agent()
            └─ executor.run(message, context)
                 │
                 ├─ P1 (AGENT_SYSTEM_PROMPT, 3247) 或 P2 (LEGACY, alias)
                 ├─ + {scoring_criteria}     ← P7/P8
                 ├─ + {action_guardrails}    ← P9
                 ├─ + {skills_section}       ← 激活技能指令
                 ├─ + {language_section}     ← P19
                 ├─ + {market_guidelines}    ← market_context
                 └─ + context data (factor_profile, news, etc.)
                 │
                 └─ run_agent_loop(tools + LLM)
```

### 3.3 Path B：问股

```
POST /api/v1/agent/chat/stream
  └─ _build_executor()
       └─ AgentExecutor.chat(message, session_id, context)
            │
            ├─ P3 (CHAT_SYSTEM_PROMPT, 856) 或 P1 (当使用 AGENT 模式)
            ├─ + {scoring_criteria}
            ├─ + {action_guardrails}
            ├─ + {skills_section}
            ├─ + {language_section} (chat_mode=True)
            └─ + context data (factor_profile from _load_stock_context)
            │
            └─ run_agent_loop(tools + LLM)
```

### 3.4 Path C：大盘复盘

```
MarketAnalyzer.run_daily_review()
  └─ generate_market_review(overview, news, ...)
       └─ _build_review_prompt()              ← P27 (~2500 字符)
            │  (自定义构建, 不引用任何标准 prompt 块)
            ├─ 指数行情
            ├─ 板块表现
            ├─ 市场新闻
            ├─ 昨日成交额参考
            ├─ 上期交易计划
            ├─ 资金流向
            └─ 自选股数据
            │
            └─ analyzer.generate_text(prompt)  ← 直接 LLM, 无 system prompt
```

### 3.5 多 Agent 路径（ARCH=multi，当前未启用）

```
ArchOrchestrator.generate_dashboard()
  ├─ P11 TechnicalAgent.system_prompt() → 技术分析 JSON
  ├─ P12 IntelAgent.system_prompt() → 情报 JSON
  ├─ P13 RiskAgent.system_prompt() → 风险 JSON
  ├─ P14 PortfolioAgent.system_prompt() → 组合 JSON
  ├─ P15 SkillAgent.system_prompt() → 技能 JSON
  └─ P16 DecisionAgent.system_prompt() → 综合决策 JSON
```

---

## 4. 占位符注入关系

### 4.1 executor.py 中的占位符

```
AGENT_SYSTEM_PROMPT 的占位符 (executor.py:56):
┌────────────────────────────────────────────────────────────────────┐
│ {market_role}          ← get_market_role(stock_code, lang)        │
│ {market_guidelines}    ← get_market_guidelines(stock_code, lang)  │
│ {default_skill_policy} ← prompt_state.default_skill_policy        │
│ {skills_section}       ← skill_manager.get_skill_instructions()   │
│ {scoring_criteria}     ← prompt_shared.SCORING_CRITERIA           │
│ {action_guardrails}    ← prompt_shared.ACTION_GUARDRAILS          │
│ {language_section}     ← _build_language_section(report_language) │
└────────────────────────────────────────────────────────────────────┘

CHAT_SYSTEM_PROMPT 的占位符 (executor.py:171):
┌────────────────────────────────────────────────────────────────────┐
│ 同上，除 {default_skill_policy} 和 {skills_section} 外            │
└────────────────────────────────────────────────────────────────────┘
```

### 4.2 analyzer.py 中的占位符

```
SYSTEM_PROMPT 的占位符 (analyzer.py:1599):
┌────────────────────────────────────────────────────────────────────┐
│ {market_placeholder}   ← market_role 字符串                        │
│ {scoring_criteria}     ← prompt_config.SCORING_CRITERIA           │
│ {action_guardrails}    ← prompt_config.ACTION_GUARDRAILS          │
│ {language_section}     ← _build_language_section(report_language) │
└────────────────────────────────────────────────────────────────────┘

LEGACY_DEFAULT_SYSTEM_PROMPT (analyzer.py:1468):
┌────────────────────────────────────────────────────────────────────┐
│ 同上 + {default_skill_policy_section}                             │
│          ← CORE_TRADING_SKILL_POLICY_ZH (P17)                     │
└────────────────────────────────────────────────────────────────────┘
```

### 4.3 注入源与消费端对照

| 注入源 | P1 AGENT | P3 CHAT | P4 ANALYZER | P5 LEGACY | P16 DECISION |
|:-------|:--------:|:-------:|:-----------:|:---------:|:------------:|
| `{market_role}` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `{market_guidelines}` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `{skills_section}` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `{default_skill_policy}` | ✅ | ❌ | ❌ | ✅ | ❌ |
| `{scoring_criteria}` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `{action_guardrails}` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `{language_section}` | ✅ | ✅ | ✅ | ✅ | ✅ |

### 4.4 {scoring_criteria} 的 4 层传递路径

```
源: prompt_config.SCORING_CRITERIA (A 或 B, 由 USE_COMPACT_PROMPT 控制)
  │
  ├─→ prompt_shared.SCORING_CRITERIA          (重新导出)
  │     │
  │     ├─→ executor.py:354 (AGENT_SYSTEM_PROMPT)     Path A2, B
  │     └─→ executor.py:408 (CHAT_SYSTEM_PROMPT)      Path B
  │
  └─→ analyzer.py:1834 (SYSTEM_PROMPT.replace)         Path A1
       │
       └─→ analyzer.py:1835 (ACTION_GUARDRAILS.replace) 同路径
```

---

## 5. 发现的 5 个问题

### 问题 1: 评分标准 4 处分散 — 修改需同步 4 个文件

**位置**: 
- `src/core/prompt_config.py:26` — SCORING_CRITERIA_A (645 字符)
- `src/core/prompt_config.py:46` — SCORING_CRITERIA_B (156 字符)
- `src/schemas/decision_scale.py:34` — CANONICAL_DECISION_SCALE_PROMPT_ZH (558 字符)
- `src/analyzer.py:149-151` — 内联在 LEGACY_DEFAULT_SYSTEM_PROMPT 中的评分描述

**影响**: 评分标准分 4 处维护，且评分↔动作映射存在细微差异：
- P7: 80-100→强烈看多, 60-79→看多, 40-59→中性, 0-39→看空
- P10: 80-100→买入/加仓, 60-79→买入/持有, 40-59→持有/观望, 20-39→减仓/卖出, 0-19→卖出/清仓
- 两套映射对"看空"的阈值不同（39 vs 39/19），"强烈看空"只在 P10 出现

**风险**: 如果未来调整评分阈值（如 52/49 校准），需要保证 4 处同步更新。

### 问题 2: analyzer.py 有两套相似 System Prompt

**位置**: `src/analyzer.py:1468` (P5, 4332 字符) vs `src/analyzer.py:1599` (P4, 3712 字符)

**差异**: P5 比 P4 多一个 `{default_skill_policy_section}` 占位符（约 620 字符的 CORE_TRADING_SKILL_POLICY_ZH）

**选择逻辑** (`_get_analysis_system_prompt`, analyzer.py:1810):
```python
if use_legacy_default_prompt:
    base_prompt = self.LEGACY_DEFAULT_SYSTEM_PROMPT  # P5
else:
    base_prompt = self.SYSTEM_PROMPT                  # P4
```

**现状**: `use_legacy_default_prompt` 通过 `_should_use_legacy_default_prompt()` 决定，当且仅当 `skills == ["bull_trend"]` 且为内置 skill 时启用。当前默认勾选全部策略 → `explicit_skill_selection=True` → `use_legacy_default_prompt=False` → 使用 P4（不含 P17）。

**冗余**: 两套 prompt 的 JSON schema、评分标准、规则部分完全一致。维护两份增加了不一致风险。

### 问题 3: 大盘复盘完全脱离标准 Prompt 体系

**位置**: `src/market_analyzer.py:1394` — `_build_review_prompt()`

**特点**:
- 完全不引用 executor.py 或 analyzer.py 的标准 prompt
- 不引用 `{scoring_criteria}` 或 `{action_guardrails}`
- 直接构建纯文本 prompt → 调用 `analyzer.generate_text()`（无 system prompt）
- 使用独立的 "data block" + "output template" 结构

**影响**: 
- 大盘复盘输出不受评分标准约束，格式自由度高
- `{scoring_criteria}` 中定义的校准阈值（52/49）不适用于大盘复盘
- 大盘复盘与整点分析/问股可能产出方向矛盾的信号

### 问题 4: 多 Agent 6 个 Prompt 处于休眠状态

**位置**: `src/agent/agents/*.py` 共 6 个专用 prompt

**状态**: 
- `ARCH=multi` 模式从未在生产环境启用
- `_build_orchestrator()` 在 factory.py:337-344 中定义但仅当 `arch == "multi"` 时调用
- 当前 `config.agent_arch = "single"`（默认值）

**6 个 prompt 的总字符**: 905 + 2001 + 1619 + 311 + 604 + 3739 = **9,179 字符**

**问题**: 这 9KB 的 prompt 定义存在于代码中但从未被执行，构成死代码。未来如果要启用多 Agent 架构，这些 prompt 需要与主 prompt 体系对齐。

### 问题 5: {skills_section} 与 CORE_TRADING_SKILL_POLICY 内容重叠

**位置**: 
- `{skills_section}` → 来自 `skill_manager.get_skill_instructions()`（动态加载 YAML skill 文件）
- `CORE_TRADING_SKILL_POLICY_ZH` → 来自 `src/agent/skills/defaults.py:24`（静态定义）

**重叠内容**: 
两者都定义了交易规则（不追高、多头排列、止损等）。`{skills_section}` 是从 YAML 配置文件中动态加载的，而 `CORE_TRADING_SKILL_POLICY_ZH` 是硬编码在 defaults.py 中的。当两个同时被注入时（仅在 LEGACY prompt 模式下），LLM 会收到两套相似但不完全一致的规则指令。

**影响**: 
- LEGACY 模式（P5）同时注入两者，可能造成矛盾
- 当前默认使用 P4（无 P17）→ 影响有限
- 但未来如果启用多 Agent 或修改 LEGACY 逻辑，需处理重叠

### 严重度排序

| 问题 | 严重度 | 影响范围 | 优先级 |
|:----|:-----:|:---------|:-----:|
| #1 评分标准 4 处分散 | HIGH | 校准修改需同步 4 处 | P0 |
| #3 大盘复盘脱管 | MEDIUM | 评分不受约束，可能方向矛盾 | P1 |
| #2 analyzer 两套 prompt | LOW | 当前路径不受影响 | P2 |
| #5 规则重叠 | LOW | 仅 LEGACY 模式受影响 | P2 |
| #4 多 Agent 死代码 | LOW | 未启用，无实际影响 | P3 |

---

## 6. 附录：各 Prompt 完整源码位置

### 6.1 文件索引

| 文件 | 包含的 Prompt |
|:----|:-------------|
| `src/agent/executor.py:56` | AGENT_SYSTEM_PROMPT (3247) |
| `src/agent/executor.py:171` | CHAT_SYSTEM_PROMPT (856) |
| `src/agent/executor.py:213` | _build_language_section (~250) |
| `src/analyzer.py:1468` | LEGACY_DEFAULT_SYSTEM_PROMPT (4332) |
| `src/analyzer.py:1599` | SYSTEM_PROMPT (3712) |
| `src/analyzer.py:1729` | TEXT_SYSTEM_PROMPT (104) |
| `src/analyzer.py:2654` | build_user_prompt (~3500, 动态) |
| `src/core/prompt_config.py:26` | SCORING_CRITERIA_A (645) |
| `src/core/prompt_config.py:46` | SCORING_CRITERIA_B (156) |
| `src/core/prompt_config.py:60` | ACTION_GUARDRAILS_A (260) |
| `src/core/prompt_config.py:79` | SIGNAL_CORRELATION_A (342) |
| `src/schemas/decision_scale.py:34` | CANONICAL_DECISION_SCALE_PROMPT_ZH (558) |
| `src/agent/agents/decision_agent.py:33/58` | DecisionAgent.system_prompt (525/3739) |
| `src/agent/agents/technical_agent.py:43` | TechnicalAgent.system_prompt (905) |
| `src/agent/agents/intel_agent.py:32` | IntelAgent.system_prompt (2001) |
| `src/agent/agents/risk_agent.py:36` | RiskAgent.system_prompt (1619) |
| `src/agent/agents/portfolio_agent.py:55` | PortfolioAgent.system_prompt (311) |
| `src/agent/agents/skill_agent.py:53` | SkillAgent.system_prompt (604) |
| `src/agent/skills/defaults.py:24` | CORE_TRADING_SKILL_POLICY_ZH (715) |
| `src/agent/skills/defaults.py:62` | TECHNICAL_SKILL_RULES_EN (421) |
| `src/services/image_stock_extractor.py:35` | EXTRACT_PROMPT (714) |
| `src/services/generation_backend_status_service.py:174` | SMOKE_PROMPTs (46/85) |
| `bot/dispatcher.py:371` | NL_PARSE_PROMPT (1284) |
| `src/market_phase_prompt.py:28` | _PHASE_PROMPTS (6×~150) |
| `src/market_analyzer.py:1394` | _build_review_prompt (~2500, 动态) |

### 6.2 按字符数排序

| 排名 | Prompt | 字符数 | 占比 |
|:---:|:-------|:-----:|:----:|
| 1 | LEGACY_DEFAULT_SYSTEM_PROMPT | 4,332 | 16.5% |
| 2 | DecisionAgent.dashboard | 3,739 | 14.2% |
| 3 | SYSTEM_PROMPT (analyzer) | 3,712 | 14.1% |
| 4 | build_user_prompt (动态) | ~3,500 | 13.3% |
| 5 | AGENT_SYSTEM_PROMPT | 3,247 | 12.3% |
| 6 | _build_review_prompt (动态) | ~2,500 | 9.5% |
| 7 | IntelAgent | 2,001 | 7.6% |
| 8 | RiskAgent | 1,619 | 6.1% |
| 9 | NL_PARSE_PROMPT | 1,284 | 4.8% |
| 10-25 | 其余 15 个 | 各 < 1,000 | 13.6% |

---

*Stage 1 报告结束。本报告为基础调研资料，供 Stage 2 深入分析使用。*
