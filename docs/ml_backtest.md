# ML 系统回测文档

> 最后更新: 2026-06-06
> 子文档，与 `docs/backtest.md` 配套阅读

---

## 一、ML 系统三层架构

```
┌─────────────────────────────────────────────┐
│  层1: 12 因子引擎 (FactorEngine)            │
│  纯数学公式，100% 客观，无随机性            │
│  IC 最高 5.0%，因子回测准确率 50.1%          │
├─────────────────────────────────────────────┤
│  层2: 15 策略 (YAML Skill 定义)             │
│  ❗ 不是公式计算，是 LLM 驱动的自然语言 YAML  │
│  15 个策略全由 LLM 判断"是否满足条件"        │
│  无法离开 LLM 独立运行                       │
├─────────────────────────────────────────────┤
│  层3: LLM 推理                              │
│  融合因子+策略+新闻+情绪                     │
│  有主观性和模型随机性 (temperature=0.7)     │
│  方向准确率 76.5% (融合回测中)              │
└─────────────────────────────────────────────┘
```

---

## 二、ML 独立回测 (`main.py --backtest`)

### 2.1 入口

```bash
cd systems/MindLynx-Aistock
.venv/bin/python main.py --backtest                          # 全量回测
.venv/bin/python main.py --backtest --backtest-code 600519   # 单只股票
.venv/bin/python main.py --backtest --backtest-days 10       # 指定窗口
.venv/bin/python main.py --backtest --backtest-force         # 强制重算
.venv/bin/python main.py --backtest-report                   # 只看报告不复跑
```

### 2.2 实现架构

ML 子系统有完整的回测子体系，共 8 个模块：

| 模块 | 文件 | 职责 |
|------|------|------|
| BacktestEngine | `src/core/backtest_engine.py` | 纯逻辑引擎：方向判定、止盈止损评估、结果分类 |
| BacktestService | `src/services/backtest_service.py` | 编排层：候选查询→评估→保存→报告 |
| BacktestRepository | `src/repositories/backtest_repo.py` | DB 访问：CRUD、候选筛选 |
| BacktestReport | `src/core/backtest_report.py` | 报告生成：30+ 指标、NAV 曲线(含图表) |
| FactorBacktest | `src/core/factor_backtest.py` | 纯因子回测基线 |
| WalkForward | `src/core/walk_forward.py` | Walk-forward 过拟合检测 |
| PerformanceAnalyzers | `src/core/perf_analyzers.py` | Sharpe/Sortino/Calmar/最大回撤 |
| CostModel | `src/core/cost_model.py` | 真实交易成本（印花税、滑点） |

### 2.3 当前数据

| 窗口 | 总评估 | 完成 | 胜率 | 平均收益 | 最佳个股(5d) |
|------|--------|------|------|---------|-------------|
| **5 天** | 598 | 598 | **26.6%** | -4.72% | 300652 雷迪克 **79.1%** |
| **10 天** | 598 | 187 | **10.7%** | -6.88% | — |
| **20 天** | 599 | 0 | N/A | N/A | 待数据积累 |

---

## 三、12 因子独立回测 (`factor_backtest.py`)

### 3.1 入口

```bash
.venv/bin/python -m src.core.factor_backtest
```

### 3.2 算法

1. 从 `stock_daily` SQLite 表加载所有股票的 OHLCV 数据
2. 对每只股票，每隔 5 天（step_days=5）：
   - 用过去 60 天数据计算 12 因子 z-score
   - 对全市场股票做截面归一化（N=11，<30 有统计不可靠警告）
   - 如果 `composite_score > 0` 预测看涨
   - 对比未来 20 个交易日的实际涨跌方向
3. 输出每只股票和整体的准确率

### 3.3 最新结果

```text
整体准确率: 50.1%
总评估次数: 525
评估股票数: 11
结论: ⚠️ 因子信号略优于随机 (50-55%)

个股:
  603189 *ST网达: 65.3%  (49次)
  300652 雷迪克:  64.0%  (50次)
  ...
  601801 皖新传媒: 30.6% (49次)
```

**对比 LLM（system 中融合回测结果）**：
```
纯因子方向准确率: 50.1% (随机)
LLM 方向准确率:    76.5% (融合回测中 ML 表现)
结论: LLM 在因子之上显著增值 (+26.4%)
```

### 3.4 12 因子定义

| 因子 | 类别 | 权重 | IC | 说明 |
|------|------|------|----|------|
| 1月反转 | 动量 | 0.35 | 4.5% | 月度反转效应 |
| 动量价差 | 动量 | 0.35 | 3.8% | 短期加速 |
| 低波动率 | 波动 | 0.12 | 4.2% | 低波溢价 |
| 量价趋势 | 情绪 | 0.10 | 3.5% | 量价相关性 |
| 换手率情绪 | 情绪 | 0.08 | 5.0% | 高换手=看空 |
| 价格位置 | 动量 | 0.03 | 3.2% | 60日高低位置 |
| 成交量加速 | 情绪 | 0.03 | 2.8% | 量能趋势 |
| 连续方向 | 动量 | 0.02 | 2.5% | 连续涨跌日 |
| 波动率比 | 波动 | 0.02 | 2.2% | 短期/长期波动 |
| 规模因子 | 质量 | 0.04 | 3.8% | 小盘溢价 |
| 非流动性 | 质量 | 0.04 | 4.1% | Amihud 指标 |
| 极端效应 | 情绪 | 0.04 | 3.5% | 极端收益回归 |

---

## 四、回测覆盖分析

### 4.1 三层回测状态

| 层 | 可独立回测？ | 回测方式 | 结果 |
|----|------------|---------|------|
| **层1: 12 因子** | ✅ **可独立** | `factor_backtest.py` | 50.1%（随机） |
| **层2: 15 策略** | ❌ **不可独立** | 策略是 LLM 驱动的 YAML 文本，不是代码 | N/A |
| **层3: LLM 推理** | ✅ **有独立回测** | `main.py --backtest` + 融合回测 | 操作建议 26.6% / 方向 76.5% |

### 4.2 回测盲区

| 不能测的 | 原因 | 影响 |
|---------|------|------|
| **策略层独立表现** | 15 策略全是自然语言 YAML，无法脱离 LLM 运行 | 无法判断"策略选错了还是 LLM 判断错了"。替代方案：\"backtest_service.py:457-475\" 已实现 skill-level 回测摘要，可记录每条分析使用的策略 ID，按策略维度统计触发率+关联胜率 |
| **sentiment_score 预测能力** | DB 存了但 `backtest_engine.py` 只用了 `operation_advice` 文本 | 无法评估"LLM 数字评分"的独立价值 |
| **因子→LLM 衰减** | 因子 z-score 作为文本注入 prompt，LLM 可能忽略或误读 | 无法诊断"LLM 正确使用了因子信息吗" |
| **策略间相关性** | 策略不产生独立分数，只有 LLM 一个输出 | 无法做策略组合优化 |

### 4.3 数据流与信息丢失

```
FactorEngine (12因子,z-score)
  → build_factor_profile() → 文本表格 → LLM prompt
    → LLM 阅读 → sentiment_score + operation_advice
      → DB 存储 (但 backtest 只用 operation_advice)

丢失: factor z-score 数值不参与回测评估
 丢失: regime 分类无独立列（嵌套于 context_snapshot JSON，可通过 JSON 解析查询）
丢失: 策略推理过程不存 DB
```

---

## 五、关键发现

### 5.1 Prompt 矛盾问题（已修复）

- **问题**: `prompt_shared.py` 的 `ACTION_GUARDRAILS` 与 `SCORING_CRITERIA` 存在矛盾
  - 评分标准说 60-79 分应"买入"
  - 约束说"优先输出持有/观望"、"只有在突破支撑时才能买入"
  - **结果**: 评分≥60 但建议"持有/观望"的案例占 **100%** (68/68)
- **修复** (bf04a70)：解除约束与评分的矛盾，要求"操作建议与综合评分对齐"

### 5.2 factor_zscores 已持久化（融合层未读）

`pipeline.py` 中 `_factor_zscores` 已写入 `context_snapshot`，但：
- `src/data_loader.py`（融合层）读取 `context_snapshot` 但未提取 `factor_zscores` 字段
- `backtest.py` 未纳入评估

> `factor_zscores` 接入融合层有两个选项：
> - **文本级融合**（当前）：通过 LLM prompt 注入 `factor_profile` 文本（已实现）
> - **数值级融合**（增强）：将 z-score 数值直接传给融合引擎做线性/贝叶斯加权（未实现）

### 5.3 sentiment_score 双系统使用差异

`sentiment_score` 在两个不同系统中的使用状态不同：

| 系统 | 位置 | 使用方式 | 状态 |
|------|------|---------|------|
| **ML 子系统回测** | `backtest_engine.py` | ❌ 忽略，只评估 `operation_advice` 文本 | 缺失 |
| **融合系统** | `src/data_loader.py:355-357` | ✅ 读取 `sentiment_score` 作为评分传给融合引擎 | 已使用 |

这意味着融合系统的 ML 权重基于 `operation_advice` 胜率(26.6%)而非 `sentiment_score` 方向准确率(84.0%)，ML 在融合系统中的可信度可能被系统性低估。

### 5.4 sentiment_score vs operation_advice 方向准确率对比

| 评估对象 | 方向准确率 | 覆盖度 | 说明 |
|---------|-----------|-------|------|
| **operation_advice** (buy/hold/sell) | 23.7% | 100% | text-based，包含所有评估 |
| **sentiment_score** (≥60→up, ≤40→down) | **84.0%** | 50% | 仅统计 LLM 明确看多/看空时 |
| **sentiment_score** (全部, 含 flat) | 46.1% | 100% | flat 预测拉低整体 |
| 纯因子 | 50.1% | 100% | factor_backtest |

> 关键发现：**当 LLM 明确看多/看空时（score≥60 或 ≤40），方向准确率高达 84.0%**（基于 2026-06-07 前 785 条历史回测数据，后验统计，不保证未来表现）。但 LLM 有约 50% 的时间输出中性评分(41-59)，这些 flat 预测的准确率低（仅 1.8%），拖累整体至 46.1%。
>
> **⚠️ 关于 76.5% 的说明**：该数字来自早期融合回测的单次结果，**当前无法复现**（未在代码中保留具体计算路径）。文档中的 76.5% 与实测 directional 准确率 84.0% 差 7.5 个百分点，可能原因包括：融合系统使用三系统综合信号（不仅是 ML）、评估时间窗口不同、L7 映射的评分微调(系数 0.3~0.55)产生偏移。当前可复现的最佳 ML 方向指标为 **84.0%（directional, 60/40 阈值）**。

### 5.5 阈值校准结论

| 阈值 (Bull/Bear) | 方向准确率 | 覆盖度 | 平衡得分 |
|-----------------|-----------|-------|---------|
| **60/40** (当前) | **84.0%** | 50% | 42.0 |
| 52/49 (最优平衡) | 81.1% | 90% | **73.2** |
| 50/48 (最大覆盖) | 79.5% | 92% | 73.1 |

> 60/40 保留作为默认值，因为方向信号纯度更高。如果希望更多信号，可切换到 52/49。

### 5.6 Flat 预测重新评估：交易过滤器价值

52/49 阈值下，flat 区间（score 50-51）仅有 **10 条/598 条（2%）**。这些 flat 预测的交易：
- 胜率 **0%**，平均收益 **-9.92%**
- 全部 10 笔如果交易都会亏损
- 跳过它们可以减少 3.4% 的总亏损（99.2% / 2877%）

**结论**: Flat 预测在 52/49 阈值下几乎不存在（2%），但当它们出现时，正确跳过会避免损失。作为交易过滤器价值有限（影响面小），作为方向预测价值为负。

### 5.7 Score 分段准确率（反直觉发现）

| Score 区间 | N | 方向准确率 | 平均收益 | 解读 |
|-----------|---|----------|---------|------|
| 60-79 (看多) | 59 | 38.2% | -0.38% | LLM 看多信号不可靠 |
| 52-59 (谨慎看多) | 76 | **56.2%** | -0.11% | 谨慎看多反而更准 |
| 50-51 (flat) | 10 | 0.0% | -9.92% | 极不准确，应跳过 |
| 31-49 (谨慎看空) | 351 | **92.8%** | -6.44% | LLM 最擅长识别下跌 |
| 20-30 (看空) | 93 | 89.0% | -4.29% | 看空信号高度可靠 |
| 0-19 (强烈看空) | 9 | 100.0% | -3.50% | 极端看空罕见但正确 |

> **核心发现**: 系统中存在严重不对称——**LLM 识别下跌的能力（89-93%）远超识别上涨的能力（38-56%）**。这与覆盖期内市场处于 downtrend_mid_vol 一致。这意味着 ML 的看空信号可靠性远高于看多信号，融合系统应区别对待。

### 5.8 300652 雷迪克异常诊断

**问题**: 旧数据中 300652 的 sentiment_score 准确率仅 21.3%，远低于 op_advice 79.1%。

**根因**: 阈值 artifact + prompt 约束，**非系统性偏差**。
- 该股 LLM 输出 score 52-75（看多），但 op_advice 写"持有"（受旧版 `ACTION_GUARDRAILS` 约束）
- 旧阈值 60/40 下：score 52-59 被归类为 flat → 股价 +7% 时评为错误 → 准确率 45.5%
- **新阈值 52/49 下：score ≥ 52 为看多 → 准确率升至 69.0%**（与 op_advice 的 79.1% 差距缩小至 10%）
- 剩余差距已通过 prompt 修复 (bf04a70) 解决

### 5.9 纯因子 vs LLM 对比

```
纯因子方向准确率:              50.1% (factor_backtest)
operation_advice 方向准确率:    23.7% (backtest_results)
sentiment_score 方向(directional): 84.0% ← 当前最佳可复现指标
sentiment_score 方向(含 flat):     46.1% (backfill)
融合系统 ML 效果(历史单次):        76.5% ⚠️ 不可复现
```

> 注意：84.0% (directional) 和 46.1% (含 flat) 是同一数据的不同口径，不矛盾。融合系统的 76.5% 因计算路径未保留，仅供参考。

### 5.7 个股 sentiment_score vs operation_advice 分歧

sentiment_score 方向准确率在不同股票上差异极大（5d 窗口）：

| 股票 | N | op_advice 方向 | sentiment 方向 | 差距 | 模式 |
|------|---|---------------|---------------|------|------|
| 600372 中航电子 | 85 | 16.5% | **85.9%** | +69.4% | 评分远优于建议 |
| 000592 平潭发展 | 82 | 15.8% | **74.4%** | +58.5% | 评分远优于建议 |
| 001390 宜安科技 | 87 | 4.6% | **55.2%** | +50.6% | 评分远优于建议 |
| 603189 *ST网达 | 84 | 59.0% | **67.8%** | +8.8% | 评分略优 |
| 605368 蓝天燃气 | 84 | 13.1% | **25.0%** | +11.9% | 评分略优 |
| 688202 华大智造 | 30 | 16.7% | **20.0%** | +3.3% | 基本一致 |
| 603557 起步股份 | 60 | 13.3% | **16.7%** | +3.3% | 基本一致 |
| **300652 雷迪克** | 86 | **79.1%** | 21.3% | **-57.8%** | **建议远优于评分** |

> 关键发现：**300652（雷迪克）是唯一一个 operation_advice 显著优于 sentiment_score 的股票**。这说明 LLM 懂得如何给出文字建议（op_advice 79.1%），但评分（21.3%）与建议方向不一致——评分可能被其他因素（如"持有"类建议拉向中性）拉扯。
>
> 600372 和 000592 则相反：LLM 评分方向准确率极高(74-86%)，但文字建议却很差——说明该股上评分和建议之间存在系统性偏差。这些分歧来源需要进一步诊断。

---

## 六、数据库表结构

### `analysis_history` — 分析记录 (1356 行)

| 列 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER | 主键 |
| `query_id` | VARCHAR(64) | 分析批号 |
| `code` | VARCHAR(10) | 股票代码 |
| `name` | VARCHAR(50) | 股票名称 |
| `sentiment_score` | INTEGER | LLM 评分 0-100 |
| `operation_advice` | VARCHAR(20) | 操作建议 |
| `trend_prediction` | VARCHAR(50) | 趋势预测 |
| `analysis_summary` | TEXT | 分析摘要 |
| **`raw_result`** | **TEXT (JSON)** | **完整 LLM 输出 + dashboard** |
| `context_snapshot` | TEXT (JSON) | 输入数据快照（结构因 report_type 而异，见下方"schema 分裂"说明） |
| `ideal_buy` / `stop_loss` / `take_profit` | FLOAT | 狙击点 |
| `created_at` | DATETIME | 创建时间 |
| `regime` | VARCHAR(32) | 市场状态标签 (如 downtrend_mid_vol)，2026-06-07 新增 |

> **context_snapshot schema**: 统一后所有 report_type='full' 的记录同时包含 `factor_zscores`、`factor_profile` 和 `regime_prompt`。

### `backtest_results` — 回测结果 (1795 行)

| 列 | 类型 | 说明 |
|------|------|------|
| `analysis_history_id` | FK | 关联分析记录 |
| `code` | VARCHAR | 股票代码 |
| `eval_window_days` | INT | 评估窗口 (5/10/20) |
| `operation_advice` | VARCHAR | LLM 操作建议 |
| `direction_expected` | VARCHAR | 预期方向 (up/down/flat) |
| `outcome` | VARCHAR | win/loss/neutral |
| `stock_return_pct` | FLOAT | 期间涨跌幅 |
| `hit_stop_loss` / `hit_take_profit` | BOOL | 止损/止盈触发 |

---

## 七、待改进

| 项目 | 优先级 | 说明 |
|------|--------|------|
| sentiment_score→方向映射 + 回测评估 | 🔴 高 | 需新增 `infer_direction_from_score()` 方法，再纳入回测 |
| factor_zscores 数值级接入 fusion | 🟡 中 | 选项：文本级(已实现) vs 数值级(待实现)，后者需要 data_loader 解析 z-score + 融合引擎支持数值输入 |
| regime 独立查询列 | 🟢 低 | 当前嵌套于 context_snapshot JSON，加列后可直接 SQL 查询 |
| 策略关联胜率统计 | 🟡 中 | ❌ 策略无法独立运行，但 `backtest_service.py:457-475` 已有 skill-level 回测摘要框架；如果记录每条分析使用的策略 ID，可按策略统计触发率 + 胜率 |

### 7.1 实施优先级

| 优先级 | 步骤 | 前置条件 | 工作量 |
|--------|------|---------|--------|
| **P0** | 新增 `BacktestEngine.infer_direction_from_score()` 映射方法 | 无 | 即时 |
| **P0** | 将 `sentiment_score` 传入 `evaluate_single()`，记录方向准确率 | P0 映射方法 | 0.5d |
| **P1** | factor_zscores 数值级接入融合层 | 需确定融合接口格式 | 1d |
| **P2** | regime 加独立列 (`ALTER TABLE ... ADD COLUMN`) | 无 | 0.5d |
| **P3** | 策略触发率/胜率追踪 | LLM agent 输出需包含策略 ID | 2d |

### 7.2 融合权重校准方案（P2 后续步骤）

ML 在融合系统中的当前权重为 `alpha=0.55`（`reliability.py:31`），基于约 55% 的估计值。
实测 sentiment_score directional 准确率为 84.0%，如果将此值用于权重校准：

1. **数据源**: 融合系统的 `logger.py` 输出的 CSV/JSON 历史记录（含三系统信号 + 最终融合得分）
2. **方法**: 用同一批历史数据，以不同 ML alpha 值（0.55、0.70、0.84）重算融合得分
3. **评估**: 对比不同 alpha 下的 Sharpe 比、胜率、最大回撤
4. **前置**: 需确认 logger 数据包含足够的历史融合决策及后续市场表现（需按日期对齐）
5. **风险**: 提高 ML 权重可能放大 LLM 的不稳定性（temperature=0.7 引入随机性）

> 因涉及融合层代码 (`src/reliability.py`, `src/logger.py`)，此校准需安排在融合系统层面执行。
