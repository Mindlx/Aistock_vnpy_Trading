# ML 子系统 Prompt 全链条审计 — Stage 3 c1skill 论证报告

> **目的**: 对 Stage 2 的 5 个修复方案进行 c1skill 交叉层论证  
> **状态**: 论证完成，方案已修正/确认  
> **日期**: 2026-07-27

---

## 问题 #1: 评分标准 4 处分散

### Stage 0 — 原架构意图

`SCORING_CRITERIA_A` 最初定义在 `prompt_config.py` 作为**回测校准的产物**（`@calibration 598样本`）。`CANONICAL_DECISION_SCALE_PROMPT_ZH` 定义在 `decision_scale.py` 作为**决策动作映射表**，两者设计意图不同，但内容高度重叠。这是增量积累的结果，非刻意差分。

**判定**: 偶然积累，非刻意设计。

### Stage 2 — 跨学科证据

| 领域 | L1 著作 | 支持论点 |
|:-----|:--------|:---------|
| 统计学习 | Hastie《ESL》§7.10 | 同一参数在不同位置重复定义 = 自由度膨胀 → 过拟合风险上升 |
| 量化管理 | Grinold & Kahn《APM》§4 | 信号定义必须单一来源（single source of truth），否则回测校准失效 |
| 认知科学 | Kahneman《TFS》Ch.7 | WYSIATI: 维护者只会看到其中一个来源，修改时忽略其他 |
| 软件工程 | Derman《Models.Behaving.Badly》 | 模型参数分散 → 模型风险不可控 |

**独立收敛**: 4 个领域从不同角度指向同一结论——单一来源是必要非可选的。

### Stage 4 — 反方论据

**反方**: 两套评分映射服务于不同目的（`SCORING_CRITERIA` 用于 LLM 评分参考，`DECISION_SCALE` 用于后端决策映射），分开维护更灵活。
→ **接受反方合理性。** 决策量表确实引入了更细的看空分档（0-19/20-39），这是评分标准中没有的粒度。如果简单删除 `DECISION_SCALE` 会丢失信息量。

**修正方案**: 不是删除 `DECISION_SCALE`，而是让它**引用** `SCORING_CRITERIA` 的定义：

```python
# decision_scale.py — 引用方式而非独立定义
from src.core.prompt_config import SCORING_CRITERIA_A
# CANONICAL_DECISION_SCALE = SCORING_CRITERIA_A + 额外动作映射
```

这样评分阈值来自单一源头，动作映射（买入/持有/卖出）在 `decision_scale.py` 中扩展。

### Stage 5 — 方案验证

原方案评分: P2（2 小时，低风险）
修正后方案: P2（1.5 小时，零风险）— 只加 import 引用，不删除任何功能。

### Stage 7 — 设计一致性

**判定**: ✅ 增强。补全了"定义分离但数据一致"的设计缺失。

---

## 问题 #2: analyzer.py 两套相似 System Prompt

### Stage 0 — 原架构意图

`LEGACY_DEFAULT_SYSTEM_PROMPT` 是早期版本的唯一 prompt。`SYSTEM_PROMPT` 是在重构时分离出来的"去掉 CORE_TRADING_SKILL_POLICY 的纯净版"。两者共存是**重构残留**，不是刻意差分。

**判定**: 偶然积累，重构未完成。

### Stage 2 — 跨学科证据

| 领域 | L1 著作 | 支持论点 |
|:-----|:--------|:---------|
| 软件工程 | Derman《Models.Behaving.Badly》 | 死代码是有成本的——每次修改都需要理解"为什么有两份"，认知负载↑ |
| 量化管理 | Carver《Systematic Trading》Ch.3 | 交易系统的规则应无歧义。两份相似 prompt 给 LLM 创造了"选择哪份"的自由度 |

### Stage 4 — 反方论据

**反方**: LEGACY 模式作为 fallback 保留了"无技能选择"场景的兼容性。删除或过度简化 LEGACY 可能破坏回退路径。
→ **接受。** LEGACY 不能被删除，但可以改薄。原方案（`LEGACY = SYSTEM_PROMPT + 一行`）保留了兼容性。

### Stage 5 — 方案验证

原方案: P2（30 分钟，零风险）✅ 维持不变。

### Stage 7 — 设计一致性

**判定**: ✅ 增强。消除分支认知负载，补全重构未完成的工作。

---

## 问题 #3: 大盘复盘脱管

### Stage 0 — 原架构意图

大盘复盘 (`_build_review_prompt`) 与整点分析/问股的设计目标不同：前者是**纯文本市场综述**，后者是**结构化个股分析**。大盘复盘不需要输出 JSON schema，所以没有复用标准 prompt。这是**刻意 **的架构选择。

**判定**: 刻意设计，非偶然。

### Stage 2 — 跨学科证据

| 领域 | L1 著作 | 支持论点 |
|:-----|:--------|:---------|
| 认知科学 | Kahneman《TFS》Ch.22 | 对同一市场，LLM 在无约束（大盘复盘）和有约束（整点分析）下可能输出方向矛盾的信号。这是 WYSIATI 的典型表现 |
| 量化管理 | Grinold & Kahn《APM》§6 | 同一系统的信号输出必须有一致性约束，否则用户会困惑于"哪个信号正确" |
| 因果推理 | Pearl《Book of Why》 | 大盘复盘的操作建议和整点分析的评分是同一因果链的输出，应共享约束条件 |

### Stage 4 — 反方论据

**反方**: 大盘复盘是纯文本叙述，不需要评分标准。给它加约束会让它产生评分式的结论，破坏"市场综述"的定位。
→ **部分接受。** 评分标准是用分数约束"强烈看多"的定义域，不是要求大盘复盘输出分数。`ACTION_GUARDRAILS`（操作护栏）中的"不编造数据""止损优先"等规则对大盘复盘同样适用。

**修正方案**: 仅追加 `ACTION_GUARDRAILS`，不追加 `SCORING_CRITERIA`。这样大盘复盘受操作约束保护，但不被评分标准束缚。

```
原方案: SCORING_CRITERIA + ACTION_GUARDRAILS
修正后: ACTION_GUARDRAILS 仅 → 15 分钟
```

### Stage 7 — 设计一致性

**判定**: ✅ 增强。大盘复盘仍是自由文本，但受操作护栏保护。不违背原架构意图。

---

## 问题 #4: 多 Agent 休眠

### Stage 0 — 原架构意图

`ARCH=multi` 是系统设计时规划的"未来架构"，6 个 Agent prompt 是为多 Agent 架构预置的占位。它们不是死代码，是**预留的扩展点**。

**判定**: 刻意设计，且标注了启用条件（`config.agent_arch == "multi"`）。

### Stage 2 — 跨学科证据

| 领域 | L1 著作 | 支持论点 |
|:-----|:--------|:---------|
| 软件工程 | Derman《Models.Behaving.Badly》 | 预留扩展点有维护成本，但保留比删除好——删除后重建成本更高 |
| 量化管理 | Carver《Systematic Trading》Ch.10 | 系统复杂度应与策略复杂度匹配。当前 single 架构够用，multi 是 over-engineering |

### Stage 4 — 反方论据

**反方**: 保留占位代码是技术债。如果不计划启用，应该删除。
→ **接受反方合理性**，但考虑到上游 fork 可能依赖 multi 架构，删除会导致 merge 冲突。

**修正方案**: 保持原方案不变——加日志标记，不做代码删除。

### Stage 7 — 设计一致性

**判定**: ✅ 维持。不违背原架构意图，仅增加运维可见性。

---

## 问题 #5: skills_section 与 CORE_TRADING_SKILL_POLICY 重叠

### Stage 0 — 原架构意图

`CORE_TRADING_SKILL_POLICY_ZH` 是早期版本的"硬编码交易规则"（在 YAML skill 系统之前）。`{skills_section}` 是后来的 YAML 动态加载系统。两者是**不同时期的产物**，共存是因为 LEGACY prompt 从未被清理。

**判定**: 偶然积累，历史遗留。

### Stage 2 — 跨学科证据

| 领域 | L1 著作 | 支持论点 |
|:-----|:--------|:---------|
| 认知科学 | Kahneman《TFS》Ch.7 | LLM 面对两套相似但不完全一致的规则时，会产生"选择困惑"，可能随机选择其中一套遵守 |
| 量化管理 | Grinold & Kahn《APM》§4 | 规则冗余 = 信号质量下降。同一指令出现两次等价于给该指令双倍权重 |

### Stage 4 — 反方论据

**反方**: 当前 LEGACY 路径不启用，重叠无实际影响。
→ **接受。** 但这是偶发性的零维护成本修复，不做的话未来某天 LEGACY 启用时会突然出问题。

### Stage 5 — 方案验证

原方案: P1（30 分钟）✅ 维持不变。

### Stage 7 — 设计一致性

**判定**: ✅ 增强。清理历史遗留，原架构意图（LEGACY 作为回退路径不受影响）。

---

## 总结：修复方案调整

| 问题 | 原方案 | 论证后调整 | 工作量 |
|:-----|:-------|:----------|:-----:|
| #1 | 统一到 prompt_config | ✅ 维持 + 增加引用式而非删除 | 1.5h |
| #2 | LEGACY 薄封装 | ✅ 维持不变 | 30min |
| #3 | 追加 SCORING+GUARDRAILS | 🔄 **改为仅追加 GUARDRAILS** | 15min |
| #4 | 加日志标记 | ✅ 维持不变 | 15min |
| #5 | LEGACY 跳过 skills | ✅ 维持不变 | 30min |

**第一阶段执行顺序调整后**（基于 c1skill 论证）：

```
P0: #1 评分标准统一 → 1.5h  ✓
P1: #3 大盘复盘追加 GUARDRAILS → 15min  (修正:仅护栏,不评分)
P2: #2 LEGACY 薄封装 → 30min
P2: #5 规则重叠 → 30min
P3: #4 多 Agent 日志 → 15min
```

要不要开始执行第一阶段？
