# LLM Prompt 注入优化研究

> **状态**: **已关闭** 🟢 — 实测压缩比约 23%，相对 128k 上下文窗口影响可忽略  
> **最后更新**: 2026-07-23  
> **来源**: `c1test` 审计发现问题  
> **结论**: B 版已准备完毕，但不启用。收益（−1,250 tokens）不抵复杂度增加

---

## 1. 背景

全量审计发现当前 LLM agent 的单次分析提示词总大小约 **17,000-22,000 chars**（1策略活跃）到 **60,000+ chars**（17策略全开）。部分内容存在冗余、重复教育、描述过于啰嗦的问题。

**核心风险**：Prompt 过大会稀释关键信息的注意力权重。LLM 在长上下文中会产生"注意力衰减"——后面的内容被前面的淹没。

---

## 2. Prompt 注入清单

### 2.1 系统提示词模板

| 来源 | 大小 | 问题 |
|:-----|:----:|:-----|
| `executor.py:AGENT_SYSTEM_PROMPT` | ~5,000 chars | JSON schema 占用 ~1,800 chars。部分约束条款可合并 |
| `executor.py:LEGACY_DEFAULT_*` | ~3,600 chars | **死代码** — 不再使用，仅在 `use_legacy_default_prompt=True` 时加载。应删除 |

### 2.2 策略指令

| 来源 | 大小 | 问题 |
|:-----|:----:|:-----|
| 单个策略 instructions | ~2,000-4,000 chars/个 | 17 个策略重叠度高。compact 模式已存在（~100 字/个） |
| 默认策略基线 | ~1,500 chars | 已精简 |

### 2.3 工具定义

| 来源 | 大小 | 问题 |
|:-----|:----:|:-----|
| `analysis_tools.py:analyze_trend` description | ~250 chars | 列出了所有参数字段名，LLM 不需要 |
| `data_tools.py:get_analysis_context` description | ~350 chars | 多段说明返回格式 |
| `backtest_tools.py` 3 个工具 | ~3,000 chars | 每个都有多段示例说明 |
| **总计 7 模块** | **~4,000-6,000 chars** | **工具描述占了 ~25% 的 total tokens** |

### 2.4 用户消息中的教育文本

| 来源 | 大小 | 问题 |
|:-----|:----:|:-----|
| `pipeline.py` 信号关联说明 | ~800chars | "量价信号多机制区分" 每次分析都注入。LLM 第一次看到有用，每天都看是浪费 |

### 2.5 评分标准

| 来源 | 大小 | 问题 |
|:-----|:----:|:-----|
| `prompt_shared.py:SCORING_CRITERIA` | ~1,800 chars | 校准注释（`{@calibration}` 标签和准确率数字）占 ~400 chars |

---

## 3. 优化候选（优先级排序）

### 🔴 P0: 精简工具描述

| 工具 | 当前 | 优化后 | 节减 |
|:-----|:----:|:------:|:---:|
| `analyze_trend` | 250 chars | 80 chars | −170 |
| `get_volume_analysis` | 280 chars | 80 chars | −200 |
| `analyze_pattern` | 200 chars | 80 chars | −120 |
| `get_realtime_quote` | 220 chars | 70 chars | −150 |
| `get_daily_history` | 150 chars | 60 chars | −90 |
| `get_analysis_context` | 350 chars | 100 chars | −250 |
| `search_stock_news` | 180 chars | 60 chars | −120 |
| 3 个 backtest 工具 | 各~300 chars | 各~60 chars | −720 |
| **合计** | **~2,580 chars** | **~830 chars** | **−1,750 chars** |

**方法**：工具 description 只保留"做什么"，去掉"返回什么"（LLM 从输出中自学习）。

### 🟡 P1: 去重信号关联说明

`pipeline.py` 中约 20 行的"量价信号多机制区分"教育文本：
- 第一次：LLM 学到"换手率情绪和放量突破不矛盾"
- 第 N 次：LLM 已经知道了

**方案**：移到 prompt_shared.py 中，与 SCORING_CRITERIA 同级。只在 `analysis_history` 为空（首次分析）时注入。

### 🟡 P2: 合并相近策略

| 策略对 | 重叠度 | 方案 |
|:-------|:------:|:------|
| `volume_breakout` + `bottom_volume` | 高（都用量能分析）| 合并为一个策略，用条件区分 |
| `bull_trend` + `ma_golden_cross` | 高（均线分析）| `ma_golden_cross` 可作为 `bull_trend` 的子条件 |
| `shrink_pullback` + `box_oscillation` | 中 | 保持独立 |

**影响**：减少策略总数，降低 LLM 选择负担。但需保留用户可理解性。

### 🟢 P3: 清理 Legacy Prompt

`executor.py` 中两套模板：
- `AGENT_SYSTEM_PROMPT`（新，5,000 chars，使用中）
- `LEGACY_DEFAULT_AGENT_SYSTEM_PROMPT`（旧，3,600 chars，**死代码**）

**方案**：删除 Legacy，减少 3,600 chars 的维护负担。

### 🟢 P4: 压缩评分标准

`SCORING_CRITERIA` 中 4 条校准注释：
```
{@calibration 598样本回测: 52/49阈值}
```
每轮注入都在。移入文档后只在 prompt 中保留数值。

---

## 4. 实施前提

在实施任何优化前，需回答：

1. **工具描述精简后，LLM 是否会误用工具？** — 需要 A/B 对比测试
2. **删除 Legacy Prompt 后是否有回退场景？** — 检查所有 `use_legacy_default_prompt` 引用
3. **策略合并后用户是否还能理解？** — YAML 文件名和 display_name 需要保留
4. **缩减教育文本后，新用户（首次分析的股票）是否有足够背景？** — 条件注入代替全量注入

---

## 5. 验证方法

优化后必须跑 `c1test --full` 对比：

| 指标 | 优化前 | 优化后 | 目标 |
|:-----|:------:|:------:|:----:|
| Prompt 大小 | 17-60k chars | TBD | −20% |
| ML sentiment 准确率 | 62.3% | TBD | 不下降 |
| 因子准确率 | 55.1% | TBD | 不下降 |

---

## 6. 版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-23 | v1 | 初始文档。基于 c1test 审计发现的 prompt 优化候选清单 |
