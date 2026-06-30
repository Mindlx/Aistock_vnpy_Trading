# ML LLM 定位修正：从独立判断者到翻译官+扩展者

> 分析日期: 2026-06-30
> 分析方法: Oracle 深度分析 → c1skill 8 阶段对抗验证 → 轨迹存档
> 涉及文件: `factor_engine.py`, `analyzer.py`, `pipeline.py`, `normalizer.py`, `c1test.py`
> 当前基线: sentiment_score 67.7%, operation_advice 27.5% (40pp gap)

---

## 一、问题发现

### 1.1 症状

c1test 统一回测首次暴露了 ML 子系统内部的结构性问题：

| 指标 | 准确率 | 测量方式 | 样本量 |
|------|:------:|---------|:------:|
| sentiment_score 方向 | **67.7%** | T+1 日涨跌比对（>=52看多/<=48看空） | 886 |
| operation_advice 方向 | **27.5%** | 5天窗口 backtest direction_accuracy | 1004 |
| 差距 | **40pp** | — | — |

同一个 LLM、同一份 prompt、同一个分析请求——但评分数值和文本建议存在 40 个百分点的偏差。

### 1.2 根因分析

**Prompt 信息结构严重失衡：**

```
LLM 收到的数据：
  外部数据（OHLCV 表、均线、实时行情、筹码、资金流向、财报、新闻、市场背景）
  → ~5000+ 字符
  自身系统的 12 因子分析（factor_profile）
  → 2 行 ("### 量化因子评分\n综合: +0.45 ↑ 偏多")
```

factor_profile 的注入位置在 prompt 尾部——在新闻、市场背景、分析任务指令、输出语言要求、信号相关性提示**之后**。LLM 在读完海量外部数据后，最后才看到自己系统的定量结论。

**更深层的定位问题：** LLM 被配置为"独立判断者"（基于注入数据独立分析），但应该被配置为"翻译官+扩展者"（翻译自身客观/半客观分析结论，用外部情报做注释补充）。

### 1.3 架构层根因

原始架构设想是 ML 内部两条独立线——客观/半客观一条、LLM 一条——期待独立验证、互相补充、1+1>2。但实际运行时：

- 客观/半客观线的 12 因子分析被高度压缩（2 行 → lossy compression）
- 策略 Agent 输出完全未桥接到 LLM context（SkillManager 中的数据未注入 prompt）
- LLM 线被迫基于外部数据独立判断，而非翻译自身分析

---

## 二、Oracle 分析：四阶段修复方案

### Phase 1 — 扩展 factor_profile (Quick Win, 1-2h)

`factor_engine.py:707` 的 `build_factor_profile()` 当前输出仅 2 行。12 因子的 `z_scores` 完整存在于 `FactorResult.z_scores` 字典中，但未被暴露给 LLM。

**改动**：
- 输出 12 因子的中文名 + z_score + 方向箭头 + 分位
- 按 category 分组（趋势/波动率/流动性/动量等）
- 注明因子置信度（基于 bootstrap CI）
- 加入制度状态（regime state）对因子的条件影响

### Phase 2 — 重构 Prompt 结构 (Medium, 1d)

当前注入顺序（analyzer.py:3040-3069）：
```
factor_text → ly_text → regime_text → position_text → allocation_text → knowledge_text
```
全部位于新闻、市场背景、分析任务指令、输出语言要求**之后**。

**改动**：将 factor_profile 移到新闻段（line 2900）之前，形成：
```
技术数据 → 📊 系统量化分析（factor+regime）→ 📰 外部情报佐证 → 分析任务
```

并在 factor_profile 前加锚定指引："以下量化因子评分是本系统基于 60 日 OHLCV 数据计算的结果，你的 sentiment_score 和 operation_advice 应以此为主要方向依据。"

### Phase 3 — 桥接 Agent 输出 (Medium, 2d)

策略 Agent（SkillAgent）产出的数据在 SkillManager 内存中（`signal/confidence/conditions_met/conditions_missed/score_adjustment/reasoning`），从未桥接到 LLM prompt context。

**改动**：将策略 Agent 的评估结论提取为 `strategy_summary` 字段，注入 `initial_context`。

### Phase 4 — c1test 验证 (Quick, 1h)

建立 before/after 测量框架，对比：
- ML operation_advice direction_accuracy
- ML sentiment_score accuracy
- 融合方向准确率
- 分歧场景准确率

---

## 三、c1skill 对抗验证

### 3.1 关键修正

c1skill 对 Oracle 方案做了 3 项重要修正：

| # | Oracle 原方案 | c1skill 修正 | 依据 |
|:-:|-------------|-------------|------|
| 1 | 40pp 差距是"LLM 翻译损失" | **测量方式不同**：sentiment 用 T+1 日（短线），operation 用 5 天窗口（中线） | `c1test.py:351-399` vs `c1test.py:351-369` |
| 2 | 4 阶段覆盖所有路径 | **Agent 路径未被覆盖**——策略 Agent 输出在 SkillManager 中，未注入 prompt | `skill_agent.py` vs `pipeline.py` 无 agent_score 桥接 |
| 3 | 全量因子注入无害 | **过度锚定风险**——LLM 可能变成因子数据的"复读机"，丧失新闻面消化器的增值作用 | factor IC 上限 ~4-5%/月 |

### 3.2 反方论据与回应

**反驳 1："40pp 差距无所谓，operation_advice 在融合权重仅 20%"**
→ 混淆了"路径权重"和"子系统权重"。ML 总权重 0.50（最大权重）。operation_advice 虽在 ML 内部只占 20% 路径，但决定了 L7 类别映射方向。27.5% 的 5 日准确率意味着 ML 在中线维度 70%+ 的时间在反向判断。

**反驳 2："扩展 factor_profile 增加 token 成本，无保证效果"**
→ 这不是"增加信息"而是"恢复信息平衡"。当前 prompt 中系统内部产出与外部数据比例约 1:50，目标 1:5。Token 增加预估 ~800-1200 chars，DeepSeek API 日成本增加约 ¥0.005，可忽略。

**反驳 3："移动 factor 位置是货物崇拜式 prompt engineering"**
→ 前提正确——纯位置移动不够。但方案是**先扩展内容（12因子逐项）再重新定位**，不是纯粹坐移。扩展后的因子剖面是"系统自身的 12 维度分析框架"，需要 LLM 先吸收再解读外部信息。

### 3.3 方案对比

| 方案 | 优势 | 劣势 |
|------|------|------|
| **A: Oracle 4-Phase** | 渐进式，每步可验证 | P3 耗时 >2 天；Agent 路径不明确 |
| **B: 最小方案（仅加强约束）** | 零代码改动 | 过刚性；未解决信息失衡 |
| **C: 激进方案（移除 operation_advice）** | 彻底消除 gap | 丢失中文语义解释价值 |
| **✅ 推荐: A+C 混合** | 兼顾修复与实用 | — |

---

## 四、最终推荐：3 个 Action 并行执行

### Action 1 — 扩展+重排 factor_profile [1-2h] ✅ 已完成

| 改动点 | 位置 | 内容 |
|--------|------|------|
| 扩展 `build_factor_profile()` | `factor_engine.py:707` | 从 2 行→12 因子逐项（中文名+z_score+方向+分位+置信度） |
| 重排注入位置 | `analyzer.py:3040` | 从尾部移到 news section（line 2900）之前 |
| 加锚定指引 | `analyzer.py` | "以下因子评分是本系统计算结果，sentiment_score 和 operation_advice 应以此为主要依据" |

### Action 2 — operation_advice 完全退出 L7 裁决 [1h] ✅ 已完成

**最终状态（42c01fd）**：

```
ML 的 L7 得分 = 100% 来自 sentiment_score (v4.0 精度校准映射)
operation_advice = 纯文本解释器，不参与融合裁决
```

| 改动点 | 位置 | 内容 |
|--------|------|------|
| 移除方向守卫逻辑 | `fusion_engine.py` | 原 op_advice 与 sentiment 方向相反时禁用 → 改为 op_advice 完全不参与融合 |
| 保留文本路径 | `normalizer.py` | operation_advice 仍生成文本用于推送展示，但不影响 L7 得分 |
| ML 输出 | — | L7 = `normalize_mindlynx_score()` 直接输出 |

这意味着 ML 系统内部实现了彻底的"分析层与表达层分离"：
- **分析层**（sentiment_score → v4.0 精度映射 → L7 得分）：参与融合裁决 ✅ 67.7%
- **表达层**（operation_advice → 人类可读文本）：仅用于推送展示 📝 27.5%（不重要了）

### Action 3 — c1test 前后对比验证 [1h]

| 指标 | 当前基线 | 成功标准 |
|------|:-------:|:--------:|
| operation_advice 准确率 | 27.5% | ≥45%（5天窗口）|
| sentiment_score 准确率 | 67.7% | 不退步 |
| 融合方向准确率 | 57.7% | 不退步 |
| 分歧场景准确率 | 55.1% | ≥55% |

回滚触发条件：sentiment_score < 60% 或融合准确率下降 >5pp。

### Agent 路径（P3）：暂缓

等待 Action 1-3 结果出来后再评估是否需单独改动。

---

## 五、风险监控

| 指标 | 来源 | 当前基线 | 警报阈值 |
|------|------|---------|---------|
| sentiment_score 准确率 | c1test ML phase | 67.7% | < 60% |
| operation_advice 准确率 | c1test ML phase | 27.5% | < 30% |
| 融合方向准确率 | c1test fusion phase | — | 下降 >5pp |
| Prompt token/分析 | LLM logs | ~5000-6000 chars | > 8000 chars |
| DeepSeek API 日成本 | API 账单 | — | > ¥0.10/日 |

---

## 六、参考文件

| 文件 | 作用 |
|------|------|
| `systems/MindLynx-Aistock/src/core/factor_engine.py:707` | `build_factor_profile()` —— 需扩展的核心函数 |
| `systems/MindLynx-Aistock/src/analyzer.py:3040` | `factor_text` 注入位置 —— 需重新定位 |
| `systems/MindLynx-Aistock/src/core/pipeline.py:1787` | `factor_profiles` 注入 —— 同步调整 |
| `src/normalizer.py:260-284` | v4.0 精度校准映射 —— 已在用 |
| `scripts/c1test.py` | 验证框架 |
| `docs/architecture/system-overview.md` | 架构全景文档（§10.1 半客观割裂发现, §10.2 定位修正） |

---

## 七、方法论

本报告遵循 c1skill 8 阶段框架完成：
1. **原架构理解** — 理解 prompt 结构的设计意图与工程局限
2. **事实声明** — 从代码审核提取 10 项可验证事实
3. **证据验证** — 逐条验证 Oracle 论断（3 项修正）
4. **缺失分析** — 发现 5 项 Oracle 未考虑的维度（Agent 路径/中文质量/测量方法学/过度锚定/成本）
5. **反方论据** — 3 项 adversarial 反驳及量化回应
6. **方案评估** — A/B/C 三方案对比，推荐 A+C 混合
7. **风险监控** — 6 项指标 + 4 条回滚条件
8. **最终结论** — 3 个 Action 可并行执行，总工作量 4-5h
