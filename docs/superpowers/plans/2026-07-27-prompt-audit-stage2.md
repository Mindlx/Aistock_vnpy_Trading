# ML 子系统 Prompt 全链条审计 — Stage 2 报告

> **课题**: Prompt 全链条审计 — 深入分析与修复方案  
> **基于**: Stage 1 发现的 5 个问题  
> **状态**: 分析完成，方案已设计（未执行）  
> **日期**: 2026-07-27

---

## 目录

1. [问题 #1: 评分标准 4 处分散](#1-问题-1-评分标准-4-处分散)
2. [问题 #2: analyzer.py 两套相似 System Prompt](#2-问题-2-analyzerpy-两套相似-system-prompt)
3. [问题 #3: 大盘复盘完全脱离标准 Prompt 体系](#3-问题-3-大盘复盘完全脱离标准-prompt-体系)
4. [问题 #4: 多 Agent 6 个 Prompt 处于休眠状态](#4-问题-4-多-agent-6-个-prompt-处于休眠状态)
5. [问题 #5: skills_section 与 CORE_TRADING_SKILL_POLICY 内容重叠](#5-问题-5-skills_section-与-core_trading_skill_policy-内容重叠)
6. [修复优先级排序与工作量估算](#6-修复优先级排序与工作量估算)
7. [附录：Stage 1 原问题描述对照](#7-附录stage-1-原问题描述对照)

---

## 1. 问题 #1: 评分标准 4 处分散

### 1.1 现状

评分标准分布在 **4 个文件、5 个位置**:

| 位置 | 变量名 | 字符 | 内容差异 |
|:-----|:-------|:---:|:---------|
| `prompt_config.py:26` | `SCORING_CRITERIA_A` | 645 | 80-100/60-79/40-59/0-39 + 校准注释 |
| `prompt_config.py:46` | `SCORING_CRITERIA_B` | 156 | 精简版，无校准注释 |
| `decision_scale.py:34` | `CANONICAL_DECISION_SCALE_PROMPT_ZH` | 558 | 80-100/60-79/40-59/**20-39/0-19** 五档 + guardrail |
| `analyzer.py:149-151` | LEGACY_DEFAULT 内联 | ~100 | "强烈买入（80-100分）" 等三段描述 |
| `analyzer.py:1599` | SYSTEM_PROMPT 内联 | 0 | 无内联，通过 `{scoring_criteria}` 引用 |

### 1.2 量表差异分析

#### 1.2.1 SCORING_CRITERIA_A (prompt_config)

```
80-100: 强烈看多 → 可参与，设止损
60-79:  看多     → 小仓参与
40-59:  中性     → 不参与
0-39:   看空     → 不参与
```

#### 1.2.2 CANONICAL_DECISION_SCALE (decision_scale)

```
80-100: 强烈看多 → 买入/加仓
60-79:  看多     → 买入/持有
40-59:  中性     → 持有/观望
20-39:  看空     → 减仓/卖出
0-19:   强烈看空 → 卖出/清仓
```

#### 1.2.3 不一致点

| 维度 | SCORING_CRITERIA_A | CANONICAL_DECISION_SCALE | 影响 |
|:-----|:-------------------|:-------------------------|:-----|
| **看空上界** | 39 | 39 | 一致 |
| **强烈看空存在?** | ❌ 无（看空只有一档 0-39） | ✅ 有（0-19 强烈看空，20-39 看空） | LLM 可能在不同路径输出不同粒度的看空级别 |
| **操作映射** | "不参与"（中性+看空） | "买入/持有/观望/减仓/卖出/清仓" | 决策量表更细，但未被评分标准引用 |
| **校准标记** | 有 `@calibration 598样本回测校准` | 无 | 决策量表未经回测验证 |

### 1.3 影响范围

- 修改变动评分标准（如调整 52/49 阈值）时，需确认 4 处同步
- LLM 在不同路径（整点分析 vs 问股）可能收到不同粒度的评分描述
- decision_scale.py 定义了决策量表但未在任何 prompt 中实际引用 → 死代码

### 1.4 修复方案

#### Phase 1（零风险，文档级）
- 在 `SCORING_CRITERIA_A` 和 `decision_scale.py.CANONICAL_DECISION_SCALE_PROMPT_ZH` 各自加注释，声明对方为"关联文件，修改须同步"

#### Phase 2（推荐，代码级）
1. **统一源**: 删除 `CANONICAL_DECISION_SCALE_PROMPT_ZH` 的评分映射部分，改为引用 `prompt_config.SCORING_CRITERIA`
2. **删除内联**: `analyzer.py:149-151` 的内联评分描述在 `SYSTEM_PROMPT` 中已无引用（`{scoring_criteria}` 覆盖），确认后删除
3. **单一入口**: `SCORING_CRITERIA_A` 作为评分标准的唯一定义源，所有其他文件统一引用它

#### Phase 3（可选）
- 将决策量表也做 `@calibration` 回测验证，确保与评分标准的映射一致
- 验证 "0-19 强烈看空" 档是否有足够样本支持

**风险**: 低。改动集中在引用路径，不涉及 LLM 行为变化。

---

## 2. 问题 #2: analyzer.py 两套相似 System Prompt

### 2.1 现状

| 属性 | SYSTEM_PROMPT | LEGACY_DEFAULT_SYSTEM_PROMPT |
|:-----|:-------------|:----------------------------|
| 行号 | `analyzer.py:1599` | `analyzer.py:1468` |
| 字符 | 3,712 | 4,332 |
| 差异 | 无 `{default_skill_policy_section}` | 含 `{default_skill_policy_section}` (+620 字符) |
| JSON schema | 完全相同 | 完全相同 |
| 评分标准 | `{scoring_criteria}` | `{scoring_criteria}` |
| 使用条件 | `use_legacy_default_prompt=False` | `use_legacy_default_prompt=True` |

### 2.2 使用条件分析

`use_legacy_default_prompt` 的判定逻辑 (`_should_use_legacy_default_prompt`, factory.py:148):

```python
def _should_use_legacy_default_prompt(
    skills_to_activate, explicit_skill_selection, skill_catalog
) -> bool:
    if explicit_skill_selection or skills_to_activate != ["bull_trend"]:
        return False
    bull_trend = next(s for s in skill_catalog if s.name == "bull_trend")
    return bull_trend.source == "builtin"
```

**当前路径 A1 的 `use_legacy_default_prompt`**:
- 通过 `resolve_skill_prompt_state()` 调用
- `explicit_skill_selection` 来自前端是否勾选技能
- 前端默认勾选全部技能 → `explicit_skill_selection=True` → `use_legacy_default_prompt=False`

**结论**: 当前默认配置下，P5 (LEGACY) 不会被使用，所有请求都走 P4 (SYSTEM_PROMPT)。

### 2.3 影响范围

- 当前无实际影响（P5 未被使用）
- 未来如果修改 `_should_use_legacy_default_prompt` 逻辑或默认 skill 配置，P5 可能突然生效
- P5 的内容（含 CORE_TRADING_SKILL_POLICY）未经当前测试覆盖

### 2.4 修复方案

#### Phase 1（零风险）
- 在 `_should_use_legacy_default_prompt` 函数加日志输出，记录何时会触发 LEGACY 路径
- 添加监控：统计使用 LEGACY prompt 的请求占比

#### Phase 2（推荐）
- 将 P5 (LEGACY) 改为**薄封装层**，而不是完整副本：

```python
# analyzer.py
LEGACY_DEFAULT_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "{language_section}",
    "{default_skill_policy_section}\n\n{language_section}"
)
```

这样 P5 不再是独立的 4,332 字符副本，而是 P4 + 一行注入。

#### Phase 3（激进）
- 完全删除 P5，将 `{default_skill_policy_section}` 直接合并到 P4 中（可选占位符，非空时才填充）

**风险**: Phase 2 零风险，Phase 3 需要确认所有引用了 P5 的地方已更新。

---

## 3. 问题 #3: 大盘复盘完全脱离标准 Prompt 体系

### 3.1 现状

`src/market_analyzer.py:1394` 的 `_build_review_prompt()` 完全自建 prompt：

```
MarketAnalyzer.generate_market_review()
  └─ _build_review_prompt()
       │  不引用:
       │    - AGENT_SYSTEM_PROMPT / CHAT_SYSTEM_PROMPT
       │    - SYSTEM_PROMPT (analyzer.py)
       │    - SCORING_CRITERIA
       │    - ACTION_GUARDRAILS
       │    - 任何标准 prompt 块
       │
       └─ analyzer.generate_text(prompt)  ← 无 system prompt
```

### 3.2 关键代码路径

```python
# market_analyzer.py:774-781
prompt = self._build_review_prompt(overview, news, ...)
review = self.analyzer.generate_text(prompt, max_tokens=8192, temperature=0.7)
```

对比其他路径的 LLM 调用方式：
- 整点分析: `analyzer.analyze(system_prompt + user_prompt)` ← 有 system prompt
- 问股: `agent_loop(system_prompt + tools)` ← 有 system prompt + function calling
- 大盘复盘: `generate_text(prompt)` ← **纯文本 prompt，无 system role 声明**

### 3.3 影响范围

- 大盘复盘输出不受评分标准约束（没有 52/49 阈值、80-100 分档等）
- 大盘复盘与整点分析可能产出矛盾信号（如整点分析说某股"强烈看多"，大盘复盘说"谨慎"）
- 大盘复盘的"操作建议"段（攻守判断、关注方向等）使用自己的措辞体系，与标准决策量表不统一

### 3.4 修复方案

#### Phase 1（零风险，推荐）
在 `_build_review_prompt()` 的输出末尾追加标准约束块：

```python
# market_analyzer.py _build_review_prompt 末尾追加
prompt += f"""

## 系统评分标准参考
{SCORING_CRITERIA}

## 操作约束
{ACTION_GUARDRAILS}
"""
```

**效果**: 大盘复盘的 LLM 输出会隐含地受评分标准约束，但 review prompt 本身结构不变。零风险。

#### Phase 2（轻度重构，可选）
将 `_build_review_prompt` 改为使用 system + user 的两段式调用：

```python
system = f"你是一位大盘复盘分析师。\n{SCORING_CRITERIA}\n{ACTION_GUARDRAILS}"
review = self.analyzer.generate_text(system + "\n\n" + prompt)
```

**区别**: Phase 1 把约束放 user prompt 末尾，Phase 2 放 system prompt。Phase 2 约束力更强（LLM 更重视 system prompt 的指令）。

#### Phase 3（重度重构，不推荐）
将大盘复盘改为复用 AGENT_SYSTEM_PROMPT 的子集。**不推荐**，因为大盘复盘的输出格式（纯文本 Markdown）与决策仪表盘 JSON 完全不同，复用反而增加复杂度。

---

## 4. 问题 #4: 多 Agent 6 个 Prompt 处于休眠状态

### 4.1 现状

6 个 Agent prompt 定义在 `src/agent/agents/*.py`：

| Agent | 文件 | 字符 | 最后修改 |
|:------|:-----|:---:|:---------|
| TechnicalAgent | `technical_agent.py:43` | 905 | 上游同步 |
| IntelAgent | `intel_agent.py:32` | 2,001 | 上游同步 |
| RiskAgent | `risk_agent.py:36` | 1,619 | 上游同步 |
| PortfolioAgent | `portfolio_agent.py:55` | 311 | 上游同步 |
| SkillAgent | `skill_agent.py:53` | 604 | 上游同步 |
| DecisionAgent | `decision_agent.py:33/58` | 525/3,739 | 上游同步 |

**触发条件**: `config.agent_arch == "multi"`（当前默认 `"single"`）

### 4.2 影响范围

- 无运行时影响（代码路径不可达）
- 6 个 prompt 总计 9,179 字符，占用代码空间但从未执行
- 未来如果要切换到 ARCH=multi，需要：
  1. 验证各 Agent prompt 内容是否与当前 prompt 体系一致
  2. 确认 {scoring_criteria} 引用是否生效（这些 Agent prompt 使用的是内联评分描述，非引用式）
  3. 需要为决策 Agent 开发专门的生成/评估流程

### 4.3 架构差异分析

**ARCH=single（当前）**:
```
LLM + ToolRegistry → Dashboard JSON
```
一个 LLM 调用完成全部工作。Tools 是函数调用。

**ARCH=multi（未启用）**:
```
TechnicalAgent → IntelAgent → RiskAgent → PortfolioAgent → SkillAgent → DecisionAgent → Dashboard JSON
```
6 个串行 LLM 调用，各 Agent 的输出是下一个 Agent 的输入。

**影响**: 
- ARCH=multi 耗时是当前的 6×（6 次 LLM 调用 vs 1 次）
- 工具调用从 function calling 改为 Agent 内部调用
- 评分标准需要在 6 个 Agent 间保持一致

### 4.4 修复方案

#### Phase 1（零风险，推荐）
在 `_build_orchestrator` 入口处加日志，标记该路径当前未启用：

```python
# factory.py:337
if arch == "multi":
    logger.warning("[AgentFactory] ARCH=multi 当前未启用")
```

#### Phase 2（清理）
- 如果确认 ARCH=multi 在可预见的未来不会启用：
  - 将 6 个 Agent prompt 移到 `_deprecated/` 子目录
  - 或加 `# @deprecated ARCH=multi not active` 标记
- 如果计划未来启用：
  - 为每个 Agent 添加引用式 `{scoring_criteria}` 注入（当前是内联硬编码）
  - 写集成测试确保各 Agent 输出 compatible

#### Phase 3（删除）
- 如果确认永不用 multi 架构，删除 6 个 prompt 文件
- **风险**: 上游 fork 可能依赖此架构，删除后 merge 冲突

**推荐**: Phase 1 + Phase 2（标记 + 迁移到引用式），保留代码但清理依赖。

---

## 5. 问题 #5: skills_section 与 CORE_TRADING_SKILL_POLICY 内容重叠

### 5.1 现状

两个来源都定义了交易规则：

| 来源 | 定义位置 | 注入方式 | 内容 |
|:-----|:---------|:---------|:-----|
| `{skills_section}` | skill_manager.get_skill_instructions() | 动态加载 YAML | 不追高、多头排列、止损等 |
| `CORE_TRADING_SKILL_POLICY_ZH` | defaults.py:24 (715 字符) | 内联拼接 | 不追高、多头排列、筹码结构等 |

### 5.2 重叠区域

**两条规则来源都包含的指令**:
1. **不追高**: 股价偏离 MA5 超过 5% 时不得建议买入
2. **多头排列**: MA5 > MA10 > MA20 是必要条件
3. **止损优先**: 任何时候触发止损条件，必须优先反映风控
4. **数据验证**: 所有数值须基于工具返回数据，不得编造

**潜在矛盾**: 当 `{skills_section}` 和 `CORE_TRADING_SKILL_POLICY` 对同一规则有不同措辞时，LLM 可能选择遵循"更具体"或"更权威"的版本。

### 5.3 注入路径

```
LEGACY_DEFAULT_SYSTEM_PROMPT (P5) 被使用时:
  {skills_section}     ← YAML 动态加载
  {default_skill_policy_section}  ← CORE_TRADING_SKILL_POLICY_ZH
  两个占位符都被填充 → LLM 收到两套规则

SYSTEM_PROMPT (P4) 被使用时:
  {skills_section}     ← YAML 动态加载
  没有 {default_skill_policy_section}
  → 只有一套规则，无重叠
```

### 5.4 影响范围

- **当前路径 A1（默认）**: P4 → 无重叠 → ✅ 无影响
- **路径 A2（AgentExecutor）**: P1 → 只有 `{skills_section}` → ✅ 无影响
- **路径 B（问股）**: P3 → 只有 `{skills_section}` → ✅ 无影响
- **仅 LEGACY 模式**: P5 → 同时有两者 → ⚠️ 有重叠

### 5.5 修复方案

#### Phase 1（零风险，推荐）
修改 `analyzer.py` 的 `_get_analysis_system_prompt()`，在 LEGACY 模式下跳过 `{skills_section}` 注入（因为规则已经包含在 CORE_TRADING_SKILL_POLICY 中）：

```python
# analyzer.py _get_analysis_system_prompt
if use_legacy_default_prompt:
    base_prompt = self.LEGACY_DEFAULT_SYSTEM_PROMPT
    # 规则已包含在 CORE_TRADING_SKILL_POLICY 中，跳过 skills_section
    skills_part = ""
else:
    base_prompt = self.SYSTEM_PROMPT
    skills_part = f"\n## 激活的交易技能\n\n{skill_instructions}"
```

#### Phase 2（彻底解决）
将 `CORE_TRADING_SKILL_POLICY_ZH` 从独立变量改为引用 `{skills_section}` ：

```python
# defaults.py
CORE_TRADING_SKILL_POLICY_ZH = (
    "## 核心交易策略基线\n"
    "{skills_section}"
)
```

但需要确保 `{skills_section}` 在 LEGACY prompt 的 format 时可用。

**推荐**: Phase 1 优先（零风险），Phase 2 可选（需要确认 format 兼容性）。

---

## 6. 修复优先级排序与工作量估算

| 优先级 | 问题 | Phase 方案 | 工作量 | 风险 | 推荐 |
|:-----:|:-----|:-----------|:-----:|:----:|:----:|
| **P0** | #1 评分标准 4 处分散 | P2: 统一到 prompt_config | 2 小时 | 低 | ✅ 立即做 |
| **P1** | #3 大盘复盘脱管 | P1: 追加标准约束块 | 15 分钟 | 零 | ✅ 立即做 |
| **P2** | #2 analyzer 两套 prompt | P2: LEGACY 改为薄封装 | 30 分钟 | 零 | ✅ 可做 |
| **P2** | #5 规则重叠 | P1: LEGACY 跳过 skills | 30 分钟 | 零 | ✅ 可做 |
| **P3** | #4 多 Agent 休眠 | P1: 加日志 + 标记 | 15 分钟 | 零 | ⭕ 等需要时做 |

### 6.1 推荐执行顺序

```
第一阶段（P0+P1，共 2.5 小时）
  ├─ #1 评分标准统一 → 2 小时
  └─ #3 大盘复盘约束 → 15 分钟

第二阶段（P2，共 1 小时）
  ├─ #2 LEGACY 薄封装 → 30 分钟
  └─ #5 规则重叠修复 → 30 分钟

第三阶段（P3，可选）
  └─ #4 多 Agent 标记 → 15 分钟
```

### 6.2 风险说明

- **#1 Phase 2**: 需要确认 `CANONICAL_DECISION_SCALE_PROMPT_ZH` 的被引用位置，确保删除评分映射部分后不破坏其他引用
- **#3 Phase 1**: 向 review prompt 追加标准约束后，需检查大盘复盘输出是否劣化（生成结果应更一致，不会劣化）
- **#2 Phase 2**: 需要确认 LEGACY prompt 的 format 行为与原来一致（`replace()` vs `format()` 区别）
- **#5 Phase 1**: 需要确认 LEGACY 模式下 `{skills_section}` 被置空后，LLM 行为是否有变化

---

## 7. 附录：Stage 1 原问题描述对照

| Stage 1 问题 | Stage 2 编号 | 分析结论 |
|:-------------|:------------|:---------|
| #1 评分标准 4 处分散 | 问题 #1 | 确认 4 处存在且两套评分映射有差异。修复方案:统一到 prompt_config |
| #2 analyzer 两套 prompt | 问题 #2 | 确认 2 套差异仅 620 字符。当前默认路径不受影响。方案:LEGACY 薄封装 |
| #3 大盘复盘脱管 | 问题 #3 | 确认完全脱管。无 system prompt。方案:追加标准约束块 |
| #4 多 Agent 死代码 | 问题 #4 | 确认 6 个 prompt 共 9,179 字符从未执行。方案:加日志/标记 |
| #5 规则重叠 | 问题 #5 | 确认 LEGACY 模式下存在重叠。当前默认路径不触发。方案:LEGACY 跳过 skills |

---

*Stage 2 报告结束。本报告为分析与方案设计，未执行任何代码修改。如需执行，请参考各问题中的推荐 Phase 方案。*
