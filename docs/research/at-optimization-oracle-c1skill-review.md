# AT 子系统优化评估 — Oracle + c1skill 联合审查

> 审计日期: 2026-06-12 | 审查范围: mind_TradingAgent 子系统 + 融合系统
> 触发: AT 回测准确率 54.2%（26/48），融合三系统中最弱
> **规则豁免声明**: 本课题研究内容及后续实施不受"零侵入"规则约束。一般规则下不得修改子系统源码，
> 但本课题本身就是对 mind_TradingAgent 子系统的修复和完善优化，属于规则的例外情况。
> 所有涉及子系统的改动均属于本课题的合理范围。

---

## 执行摘要

对 mind_TradingAgent（at）子系统进行全面审查，发现核心问题：**数据源严重不可用 + 缺乏客观信号锚定**。Akshare 的 RemoteDisconnected 失败率接近 100%，efinance 也经常不可用，10只股票中 4-5只被跳过。同时 AT 缺少 LY+ML 的客观量化信号作为 LLM 辩论的参考基准。

建议方案 B（LY+ML 客观信号注入 AT + 改为始终注入 + 保留 Agent 自有工具调用）作为后续优化方向。

---

## 一、现状：数据源严重降级

### 1.1 降级链

```
AT 数据源验证 (verify_stock):
  akshare → (失败) → efinance → (失败) → yfinance (唯一可用)
  
  fast_degrade = akshare失败 AND efinance失败 → True
```

### 1.2 实际日志证据 (ta-cron.log)

```
akshare get_stock_data(601801.SS) failed:
  ('Connection aborted.', RemoteDisconnected(...))

akshare get_indicators(300676.SZ) failed:
  ('Connection aborted.', RemoteDisconnected(...))

akshare get_indicators(600372.SS) failed: (重复20+次)

fallback news: module 'akshare' has no attribute 'stock_info_news'
  ↑ fallback函数在已安装版本中已移除 → 永远失败

A股数据 [300652]: 所有数据源不可用，跳过分析
A股数据 [605368]: 所有数据源不可用，跳过分析
A股数据 [000592]: 所有数据源不可用，跳过分析
A股数据 [603189]: 所有数据源不可用，跳过分析
```

### 1.3 现有降级注入路径

`mind_agent_wrapper.py` 已有 `_get_preloaded_context()` 函数，从 UnifiedCache + ML DB 提取 OHLCV/技术指标/基本面/新闻——但**仅当 `fast_degrade=True` 时触发**，且**缺少 LY 双模型信号**。

参考: `src/mind_agent_wrapper.py:154-206`，`src/ashare_data.py:122-125`

---

## 二、已完成的 AT A 股化改造 (5月30日)

| 改造项目 | 提交 | 具体内容 |
|----------|------|----------|
| 10个Agent prompts全改造 | 38a8ed6 | 新闻:政策优先框架；基本面:7点红旗清单；多空:北向/主力/融资盘维；组合经理:T+1流动性/涨跌停 |
| 新闻源切到东方财富 | af21f02 | get_news() 以 stock_news_em() 为主 |
| 数据源全面增强 | 511bda9 | P0:雪球+东方财富股吧；P1:北向+主力流向；P2:央行/LPR/CPI/PMI宏观查询；P3:完整技术指标 |
| akshare优先，yfinance降级 | 587c5cd | akshare→yfinance 降级链 |
| 情报系统升级 | 3fd4a23 | 巨潮公告+CCTV新闻+全市场风险提示 |

---

## 三、回测表现

| 系统 | 准确率 | 评估次数 |
|------|--------|---------|
| 融合 | 62.3% | 69 |
| Lynx (ly) | 62.8% | 43 |
| MindLynx (ml) | 77.8% | 27 |
| **TradingAgent (at)** | **54.2%** | **48** |

数据范围: 2026-05-30 ~ 2026-06-12，11天，10只股票

---

## 四、Oracle 架构分析

### 4.1 根本问题

AT 是纯 LLM 角色扮演辩论，缺少客观信号锚定。当 10 个 Agent 辩论时，缺乏独立的量化基准来判断方向。叠加数据源大面积不可用，Agent 的信息输入严重受限。

### 4.2 LY→ML 注入模式已验证

commit `a1187c0` (2026-06-12) 已实现 LY 双模型信号注入 ML 子系统的 LLM 上下文:
- `_load_ly_signals()` 读取 ly_signal.json + ly_alpha_signal.json + prob_up_log.csv
- 输出结构化 Markdown 表格含: 综合上涨概率、RF/LGB各自概率、L7得分、模型分歧度、置信度标签
- 通过 executor.py 的 `_build_user_message` 和 analyzer.py 的 `_format_prompt` 注入

### 4.3 AT 当前注入通道缺失

`mind_agent_wrapper.py` 的 `_get_preloaded_context()` 已有数据提取框架（UnifiedCache + ML DB），但:
- LY 双模型信号未包含
- 仅在 `fast_degrade=True` 时触发（降级回退路径，非始终注入）

---

## 五、改进方案评估

| 方案 | 操作 | 工作量 | 预期 | 风险 |
|------|------|--------|------|------|
| A: LY信号注入AT | _get_preloaded_context() 增加 ly_signal.json 读取 | ~90行 | AT 获得LY量化基准 | 低 |
| B: LY+ML全量注入 + 始终注入 | 方案A + 注入ML分析 + 改为始终注入非仅降级 | ~150行 | 全谱客观锚定 | 中（数据冲突风险） |
| C: 方案B + 评分极端化修复 | 方案B + normalizer增加AT中间档位 | ~200行 | 全面改善 | 高 |
| D: 先降权到0.10观察 | 改weights.yaml | ~5行 | 减少噪声 | 安全但不解决 |

### 5.1 推荐的方案 B 细节

1. 将 `should_inject = data_check.get("fast_degrade", False)` 改为始终 `True`
2. 在 `_get_preloaded_context()` 中增加读取 `ly_signal.json` + `ly_alpha_signal.json` 的 LY 双模型信号
3. 保留 Agent 调用自有工具的能力（注入是参考而非替代）
4. 修改回填 `data/realtime/at_signal.json` 的内容格式

---

## 六、引用文件

- `src/mind_agent_wrapper.py` — AT 封装器 + 注入逻辑
- `src/ashare_data.py` — A股数据降级链
- `src/normalizer.py:287-308` — AT 5级评级→L7 映射
- `systems/mind_TradingAgent/mind_tradingagent/graph/trading_graph.py` — AT 辩论管线
- `systems/mind_TradingAgent/mind_tradingagent/graph/signal_processing.py` — 信号提取
- `systems/mind_TradingAgent/mind_tradingagent/agents/utils/rating.py` — 5级评级
- `docs/research/weight-c1skill-review.md` — 之前权重和AT相关论证
- `docs/research/full-system-audit-c1skill-oracle.md` — 全系统审计
- `docs/research/archive/fusion_architecture_research.md` — Oracle 方法论源头

## 七、状态

🟡 已分析完成，待深度研究后实施

---

## 八、Sisyphus 独立审查 — c1skill 结构化论证

> 审查日期: 2026-06-12 | 方法论: c1skill (7-stage)
> 审查对象: 本报告全文 + 代码级证据验证
> ⚠️ 以下论证已考虑**规则豁免声明**（头部注明的零侵入例外）。豁免前/后的影响差异在 8.4/8.5 中说明。

### 8.1 报告质量评分

| 维度 | 评分 | 理由 |
|------|------|------|
| 问题诊断 | ✅ 8/10 | 数据源不可用、缺少锚定诊断准确；日志证据充分 |
| 方案对比 | ⚠️ 6/10 | 四方案清晰但缺验证计划和退出标准；高估注入效果 |
| 代码级证据 | ✅ 8/10 | 引用的代码位置准确，行号精确 |
| 对 AT 独立价值思考 | 🟡 4/10 | 未讨论"注入后 AT 是否失去多样性"的核心矛盾 |
| 实施计划完整性 | 🟡 5/10 | 缺验证周期、成功标准、回退计划 |

### 8.2 代码验证发现的差异

| # | 报告声明 | 代码事实 | 判定 |
|---|---------|---------|------|
| 1 | AT 结果写入 at_signal.json（一条路径） | **三条路径**: `run_daily.py:write_at_signal()` + `mind_agent_wrapper.py:run_batch_and_save()` + `data_loader.py:TradingAgentDataLoader` | ⚠️ 不完整 |
| 2 | "fast_degrade = akshare失败 AND efinance失败 → True" | `ashare_data.py:122-125`: 需 **额外 yfinance 可用** 才 True，否则 available=False 跳过 | ✅ 准确 |
| 3 | "方案B工作量 ~150行" | 新增 ~80行 + 逻辑 ~10行 + 格式 ~60行，合理 | ✅ 准确 |
| 4 | 混淆矩阵可验证 AT 错误模式 | **不存在**: 代码库无混淆矩阵代码，bt_predictions 虽存 at_correct 但未做方向级分组 | ❌ 缺失 |
| 5 | AT 个股准确率可分解 | **不存在**: backtest.py 的 per_stock 查询只追踪 fusion_correct，非 at_correct | ❌ 缺失 |

### 8.3 关键缺失（Stage 3）

**缺失 1：统计显著性检验** — 🔴 高

26/48（54.2%）在二项检验中 p≈0.67，**无法拒绝"准确率=50%"的零假设**。所有方案 A/B/C 都隐含假设"AT 有统计显著的预测能力需要增强"，但这个前提未验证。

**缺失 2：混淆矩阵 / 误差分析** — 🔴 高

只有准确率数字，不知道 AT 是偏向看多还是看空、主要在哪些场景出错。bt_predictions 表已有 `at_dir` 和 `at_correct` 字段，但无人做方向级聚合分析。

**缺失 3：AT 跳过 vs AT 中性的区别** — 🟡 中

AT 跳过某股票（无 at_signal.json 条目）→ 融合权重重分配给 ly+ml。AT 给出 Hold（L7=0）→ 拉低融合得分。两者对融合结果影响不同，报告未区分。

### 8.4 反方论据（Stage 4）

#### 报告可能高估的内容

**1. "AT 准确率经数据积累可提升"**

反方: 48 次评估 p≈0.67，统计上不显著。4/10 股票被跳过，54.2% 只对剩下 6 只有效。AT 准确率不随天数改善（逐日趋势始终在 50% 附近）。

**2. "方案 B 注入 LY+ML 可显著提升 AT"**

反方: LY 信号已以 0.30 权重直接参与融合。再注入 AT 只是让 LLM 辩论"参考"同一数据 — AT 可能照搬 LY/ML 结论，失去独立视角。注入不保证 AT 有效使用信号。

#### 报告可能低估的内容

**3. "数据冲突风险 — 中"**

> **豁免前**: 应为高。AT Agent 同时有注入数据 + 自身 yfinance 结果，LLM 处理冲突不稳定。
> 
> **豁免后**: 降为🟡中。可以修改 AT 子系统代码，在注入成功时跳过/禁用 Agent 自有 yfinance 工具调用，
> 使 AT Agent 只持有一份数据源，从根本上消除冲突。
> 
> 具体做法: 修改 `verify_stock()` 或 AT 工具路由，当 `should_inject=True` 时跳过 `ticker.history()`
> 等实时数据获取，仅依赖注入的 UnifiedCache 数据。

**4. LY→ML 注入模式类比（豁免后修正）**

| 维度 | LY→ML | LY→AT（豁免前） | LY→AT（豁免后） |
|------|-------|----------------|----------------|
| LLM 数量 | 单一分析器 | 10 个 Agent 辩论 | **可集中注入关键 Agent** |
| 数据冲突 | 无 | **有** | **可消除**（禁 Agent 自取数据） |
| 信号位置 | Prompt 显式段落 | create_initial_state → 被覆盖 | **可改到每个 Agent 的 system prompt** |
| 输出格式 | 结构化 JSON | 5 级评级 + 自由辩论 | 不变（可约束 Agent 输出格式） |

豁免后 LY→AT 注入的技术障碍大幅降低，类比从"不成立"变为"部分可参考"。

#### 假设检验

| 假设 | 报告立场 | 反方证据（豁免前） | 反方证据（豁免后） | 判定 |
|------|---------|-----------------|-----------------|------|
| "AT 有统计显著的预测能力" | 隐含 | 26/48 p≈0.67，无法拒绝随机 | 不变 | ❌ **不受豁免影响** |
| "注入客观信号能改善 LLM 辩论" | 是 | 数据冲突 + 注入位置不佳 | **可消除冲突 + 改到 system prompt** | ✅ **可行** |
| "方案 B 实施周期 < 1 周" | 是 | 代码 150 行，验证 1 个月 | 不变 | ⚠️ 验证周期仍不可压缩 |
| "注入后 AT 失去多样性" | 未提及 | 可能照搬 LY/ML | 不变 | ❌ **不受豁免影响** |

### 8.5 修正建议

**豁免规则的影响**: 方案 B/C 的技术障碍大幅降低（数据冲突可消除、注入位置可优化、10 Agent 消费可控制），
但"AT 是否有统计显著信号"这个前提不变。因此优先级重排为:

| 优先级 | 行动 | 工作量 | 前提 |
|--------|------|--------|------|
| **P0** | **统计检验**: 用已有 48 样本检验 AT 是否 > 随机 | ~10 行脚本 | 无 |
| **P0** | **方案 D**: 权重降到 0.10，运行 weight_sweep 验证 | ~5 行 | 无 |
| **P1** | **如果 P0 通过 → 方案 B**（豁免后可行，直接走全量注入） | ~150 行 | P0 证明 AT 有信号 |
| **P2** | 扩展 at_signal.json 字段（加置信度/数据源），配合 Agent 输出约束 | ~60 行 | P1 验证通过 |
| **P3** | 相关性监控（AT vs LY/ML）+ 信号多样性保障 | ~60 行 | 长期 |

### 8.6 监控指标设计

| 指标 | 当前基线 | 实施后目标 | 告警阈值 |
|------|---------|-----------|---------|
| AT 方向准确率 | 54.2% | > 58% | < 54% 或下降 |
| AT 响应率（非跳过率） | ~60% (48/69) | > 85% | < 70% |
| AT vs LY 信号相关性 | 未知 | < 0.6 | > 0.8 |
| AT vs ML 信号相关性 | 未知 | < 0.6 | > 0.8 |

### 8.7 总评

报告的**问题诊断质量很高**（数据源不可用、缺少客观锚定）。

**豁免规则降低了方案 B/C 的技术风险**（数据冲突可消除、注入位置可优化），但未改变核心前提:
**AT 是否有统计显著的预测能力尚未确认**。48 个样本的 54.2% 无法拒绝随机假设（p≈0.67）。

因此，即使技术障碍已消除，**仍需先做 P0（统计检验 + 降权）再决定是否启动注入**。
如果 AT 确实无信号，修改子系统代码也不会变出信号——只是浪费 150 行代码和 1 个月验证时间。
如果 AT 有信号，豁免后可以直接走方案 B（全量注入），不再受方案 A 的保守限制。
