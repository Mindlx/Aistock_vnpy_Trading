# 回测系统文档

> 最后更新: 2026-06-06

---

## 一、架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Aistock_vnpy_Trading 回测系统                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Level 1: 融合回测                           Level 2: 子系统独立回测  │
│  ┌─────────────────────────────┐     ┌────────┬────────┬────────┐  │
│  │ scripts/backtest.py        │     │  ly    │  ml    │  at    │  │
│  │                           │     │ backtest│backtest│   ❌   │  │
│  │ 融合信号 → 方向判定        │     │  ✅    │  ✅    │  无独  │  │
│  │ 三系统准确率对比           │     │88.9%*  │ 26.6%  │ 立系统  │  │
│  │ 分歧场景分析               │     │569样本 │1795评  │        │  │
│  │ 逐日趋势/个股排名          │     │        │ 估     │        │  │
│  └─────────────┬───────────────┘     └────────┴────────┴────────┘  │
│                │                                                    │
│         ┌──────▼──────┐                                            │
│         │ unified_cache│  ← 共享行情数据源                          │
│         │ ohlcv_cache  │                                            │
│         └─────────────┘                                            │
└─────────────────────────────────────────────────────────────────────┘

* ly 88.9% 为 in-sample 测试（模型在重叠数据集上训练和评估）
```

---

## 二、Level 1: 融合回测

### 2.1 入口

```bash
# 从项目根目录
.venv/bin/python scripts/backtest.py init        # 初始化回测数据库
.venv/bin/python scripts/backtest.py record       # 从 fusion CSV 记录预测
.venv/bin/python scripts/backtest.py check        # 匹配次日行情
.venv/bin/python scripts/backtest.py update       # record + check (每日一次)
.venv/bin/python scripts/backtest.py report       # 生成累计报告
.venv/bin/python scripts/backtest.py backfill     # 扫描历史 CSV 回填
.venv/bin/python scripts/backtest.py report --detail  # 含个股明细
```

### 2.2 数据流

```
fusion_engine.py → fusion_{date}.csv
    ↓
backtest.py record → bt_predictions 表
    ↓
backtest.py check → unified_cache.ohlcv_cache.db (T+1 行情)
    ↓
backtest.py report → 准确率报告
```

### 2.3 数据库

- **路径**: `data/backtest/bt_results.db`
- **表结构**: `bt_predictions` (60+ 列), `bt_meta`
- **当前数据量**: 60 条预测 (6 个交易日 × 10 只股票), 40 条已匹配

### 2.4 回测指标

| 指标 | 说明 |
|------|------|
| **方向准确率** | 融合/ly/ml/at 各自预测方向 vs T+1 实际涨跌 |
| **分歧场景** | 有分歧时 vs 无分歧时的融合准确率对比 |
| **信号分布** | 各 L7 信号 (neutral/bullish/bearish) 的命中率 |
| **逐日趋势** | 每日准确率变化曲线 |
| **个股统计** | 每只股票的预测次数和准确率 |
| **融合 vs 最优** | 融合系统 vs 三个子系统中最优者的对比 |

### 2.5 最新结果

```
系统               正确/      
融合             22/32      68.8%
Lynx            20/28      71.4%
MindLynx        13/17      76.5%  ← 最优
TradingAgent    10/17      58.8%

分歧场景: 分歧时 100.0% | 无分歧 67.7%
样本充足: ✅ (40/30)
```

### 2.6 自动执行

| 回测 | 触发器 | 频率 |
|------|--------|------|
| **融合回测** `backtest.py update` | 融合引擎每日19:00运行后自动调用 (`run_daily.py:484-499`) | 每日 |
| **LY 独立回测** `lynx_signal.py --backtest` | `Aistock_vnpy_Trading-lynx-backtest.timer` | 每周日 10:00 |
| **东方财富评级阈值校准** `calibrate_eastmoney_thresholds.py` | `Aistock_vnpy_Trading-eastmoney-calibrate.timer` | 每月1日 10:00 |

---

## 三、Level 2a: ly 独立回测

### 3.1 入口

```bash
.venv/bin/python systems/lynx_vnpy/lynx_signal.py --backtest
```

### 3.2 实现

在 `lynx_signal.py` 中新增 `--backtest` 模式和 `cmd_backtest()` 函数：

**算法**:
1. 对每只股票，加载训练好的 RandomForest 模型和 StandardScaler
2. 加载历史日K线数据 (Sina Finance API + parquet 缓存)
3. 从第 60 根K线开始，逐日滑动窗口预测：
   - 用 `compute_features()` 计算技术指标 (15 个特征)
   - 用 `model.predict_proba()` 输出上涨概率
   - 比较预测方向 (`prob_up ≥ 50%` = 看多) vs 实际次日涨跌幅
4. 输出每只股票的准确率，含高置信度区间（`prob_up ≥ 65% 或 ≤ 35%`）

**关键函数**:
- `_bt_predict_at(df, model, scaler, idx)` — 在历史位置 `idx` 做预测（只用 `idx` 之前的数据，无未来信息泄露）
- `cmd_backtest()` — 主流程：遍历股票 → 逐日预测 → 汇总统计

### 3.3 最新结果

```
总体准确率: 506/569 (88.9%)

个股准确率:
  605368 蓝天燃气    : 94.5% (52/55)  高置信: 93.3%
  001390 古麒绒材    : 91.4% (53/58)  高置信: 96.6%
  300652 雷迪克     : 91.4% (53/58)  高置信: 100.0%
  000592 平潭发展    : 89.7% (52/58)  高置信: 100.0%
  603189 *ST网达   : 87.9% (51/58)  高置信: 95.7%
  300676 华大基因    : 87.9% (51/58)  高置信: 100.0%
  603557 *ST起步   : 87.5% (49/56)  高置信: 88.2%
  688202 美迪西     : 87.5% (49/56)  高置信: 100.0%
  600372 中航机载    : 86.2% (50/58)  高置信: 100.0%
  601801 皖新传媒    : 85.2% (46/54)  高置信: 93.1%
```

**✅ Walk-forward 已实现**: 当前使用 expanding window walk-forward（每20天重训练一次，最少60天作为初始训练集）。`cmd_backtest()` 使用 `train_df = df.iloc[:train_end - 1]` 确保训练集不含未来数据（修复了前视偏差问题）。准确率为 out-of-sample 估计。

### 3.4 模型信息

- 算法: RandomForestClassifier (n_estimators=100, max_depth=5)
- 特征: 15 个技术指标 (RSI, MACD, ATR, 布林带, CCI 等)
- 训练数据: 每只股票 ~120 个交易日
- 模型文件: `systems/lynx_vnpy/models/{code}_model.pkl` (10 个)
- 目标: 预测次日涨跌方向 (二分类)

---

## 四、Level 2b: ml 独立回测

### 4.1 入口

```bash
# 从 MindLynx 虚拟环境运行
cd systems/MindLynx-Aistock
.venv/bin/python main.py --backtest                          # 全量回测
.venv/bin/python main.py --backtest --backtest-code 600519   # 单只股票
.venv/bin/python main.py --backtest --backtest-days 10       # 指定窗口
.venv/bin/python main.py --backtest --backtest-force         # 强制重算
.venv/bin/python main.py --backtest-report                   # 只看报告不复跑
```

### 4.2 实现架构

ML 子系统有完整的回测子体系（独立于融合回测），共 8 个模块：

| 模块 | 文件 | 职责 |
|------|------|------|
| BacktestEngine | `src/core/backtest_engine.py` | 纯逻辑引擎：方向判定、止盈止损评估、结果分类 |
| BacktestService | `src/services/backtest_service.py` | 编排层：候选查询→评估→保存→报告 |
| BacktestRepository | `src/repositories/backtest_repo.py` | DB 访问：CRUD、候选筛选 |
| BacktestReport | `src/core/backtest_report.py` | 报告生成：30+ 指标、NAV 曲线(含图表) |
| FactorBacktest | `src/core/factor_backtest.py` | 纯因子回测基线（对比 LLM vs 因子） |
| WalkForward | `src/core/walk_forward.py` | Walk-forward 过拟合检测 |
| PerformanceAnalyzers | `src/core/perf_analyzers.py` | Sharpe/Sortino/Calmar/最大回撤 |
| CostModel | `src/core/cost_model.py` | 真实交易成本（印花税、滑点） |

**数据流**:
```
AnalysisHistory → BacktestRepository.get_candidates()
    → BacktestEngine.evaluate_single() → 方向/止盈/止损判定
    → BacktestResult (DB)
    → BacktestEngine.compute_summary() → BacktestSummary (DB)
    → BacktestReportGenerator.generate() → Markdown 报告 + NAV 曲线 PNG
```

### 4.3 数据库

- **路径**: `systems/MindLynx-Aistock/data/stock_analysis.db`
- **表**: `backtest_results` (30 列), `backtest_summaries` (26 列)

#### `backtest_results` 核心列

| 列 | 类型 | 说明 |
|------|------|------|
| `analysis_history_id` | FK | 关联分析记录 |
| `code` | VARCHAR | 股票代码 |
| `analysis_date` | DATE | 分析日期 |
| `eval_window_days` | INT | 评估窗口 (5/10/20 交易日) |
| `operation_advice` | VARCHAR | LLM 操作建议 |
| `direction_expected` | VARCHAR | 预期方向 (up/down/flat) |
| `direction_correct` | BOOL | 方向是否准确 |
| `outcome` | VARCHAR | win/loss/neutral/insufficient_data |
| `stock_return_pct` | FLOAT | 期间股票涨跌幅 |
| `max_high` / `min_low` | FLOAT | 期间最高/最低价 |
| `hit_stop_loss` / `hit_take_profit` | BOOL | 是否触发止损/止盈 |

#### `backtest_summaries` 核心列

| 列 | 说明 |
|------|------|
| `scope` | overall / stock / skill |
| `eval_window_days` | 评估窗口 |
| `direction_accuracy_pct` | 方向准确率 |
| `avg_stock_return_pct` | 平均持仓收益 |
| `win_count` / `loss_count` | 盈亏次数 |
| `avg_win_pct` / `avg_loss_pct` | 平均盈亏幅度 |
| `advice_breakdown_json` | 按操作建议拆分的准确率 |

### 4.4 最新结果

| 窗口 | 总评估 | 完成 | 胜率 | 平均收益 | 最佳个股(5d) |
|------|--------|------|------|---------|-------------|
| **5 天** | 598 | 598 | **26.6%** | -4.72% | 300652 雷迪克 **79.1%** |
| **10 天** | 598 | 187 | **10.7%** | -6.88% | — |
| **20 天** | 599 | 0 | N/A | N/A | 待数据积累 |

> 注意: 5 天窗口胜率 26.6% 但方向准确率 76.5%（在融合回测中）。区别在于：ML 回测评估的是 LLM 操作建议（买入/持有/卖出）的盈亏，而融合回测评估的是 L7 方向判定（看多/看空）的对错。**LLM 的文本操作建议表现不佳，但经 L7 映射后的方向判定质量很高。**

### 4.5 Agent 集成

ML 的 Agent 系统自动使用回测数据做技能加权：

```python
# agent/memory.py → compute_skill_weights()
# 每个 Agent 技能的回测胜率自动调节其在聚合层的影响力
# 受控于 AGENT_SKILL_AUTOWEIGHT env var (默认开启)
```

---

## 五、融合回测 vs 子系统回测的关键差异

| 维度 | 融合回测 | ly 回测 | ml 回测 |
|------|---------|---------|---------|
| **评估对象** | 三系统融合后的 L7 方向 | RandomForest 上涨概率 | LLM 操作建议 |
| **判定标准** | 方向 (看多/看空/中性) | 方向 (≥50%↑ / <50%↓) | 方向 + 盈亏 |
| **数据源** | fusion CSV → unified_cache | Sina finance API → parquet 缓存 | analysis_history DB |
| **评估范围** | T+1 方向准确率 | T+1 方向准确率 | T+5/10/20 方向+盈亏+回撤 |
| **结果类型** | 方向正确/错误 | 方向正确/错误 | win/loss/neutral + 收益率 |
| **样本量** | 60 (匹配40) | 569 (in-sample) | 1,795 (598 已完成) |
| **频率** | 每日自动 | 每周日自动 (systemd timer) | 每日自动 (pipeline 中) |

---

## 六、c1test 统一回测系统 (2026-06-29)

> 从零到一的里程碑：解决三套独立回测系统各自为政、AT 无独立回测、ML sentiment 不可见三大问题。

### 6.1 架构

```
c1test.py (编排器, ~340行)
├── Phase 1: 融合回测 (子进程 backtest.py + 直查 bt_results.db)
├── Phase 2: LY 独立回测 (子进程 lynx_signal.py --backtest)
├── Phase 3: ML 独立回测 (直查 stock_analysis.db)
│   ├── backtest_summaries → operation_advice 方向准确率
│   └── analysis_history + stock_daily JOIN → sentiment_score 方向准确率 ✅
├── Phase 4: AT 独立回测 (TA JSON 日志 + stock_daily T+1 匹配) ✅ 盲区填补
├── 变化检测: 对比 last_run.json → 红黄绿告警
└── 统一报告: unified_report.json + .md + last_run.json
```

### 6.2 入口

```bash
# 快速模式（日常）：融合回测 + 缓存子系统数据
.venv/bin/python scripts/c1test.py

# 全面模式（每周/变更后）：融合 + LY + ML + AT 全量
.venv/bin/python scripts/c1test.py --full

# 只看上次报告，不重跑
.venv/bin/python scripts/c1test.py --report
```

### 6.3 盲区填补对比

| 盲区 | 之前 | 之后 |
|------|------|------|
| AT 独立回测 | ❌ 不存在 | ✅ 54.8% (34/62) |
| ML sentiment 方向准确率 | ❌ 只从融合间接看(62.3%) | ✅ 直查 **67.7%** |
| 统一回测入口 | ❌ 3 套系统不同命令 | ✅ `c1test.py` 一个命令 |
| 变化检测 | ❌ 每次人工比数字 | ✅ 自动红黄绿告警 |
| LY 格式健壮性 | ⚠️ 无保护 | ✅ 格式守卫+原始输出回退 |

### 6.4 自动执行

| 定时器 | 时间 | 命令 |
|--------|------|------|
| `c1test-daily.timer` | 工作日 20:00 | `c1test --quick` |
| `c1test-weekly.timer` | 周日 10:30 | `c1test --full` |

### 6.5 输出解读

| 指标 | 健康区间 | 说明 |
|------|---------|------|
| 融合准确率 | >55% | 三系统融合方向判定 |
| ML sentiment | >55% | LLM 评分方向准确率 (真实指标) |
| LY OOS | >50% | RF 模型 out-of-sample |
| AT 准确率 | >45% | 多智能体辩论方向 (参考值) |

**告警阈值**：
- 🔴 下降 ≥5% → regression
- 🟡 下降 2-5% 或 ML 语义差距 >30% → warning
- 🟢 提升 ≥3% → improvement

---

## 七、使用场景

| 场景 | 命令 | 说明 |
|------|------|------|
| **一键全系统回测** | `scripts/c1test.py --full` | 🆕 统一入口，推荐默认 |
| **每日快速检查** | `scripts/c1test.py` | 🆕 融合回测+缓存数据 |
| **评估模型是否退化** | `lynx_signal.py --backtest` | 每周日自动运行，也可手动触发对比历史变化 |
| **分析 LLM 建议质量** | `main.py --backtest --backtest-report` | 看 ML 独立报告 (Sharpe/回撤/NAV) |
| **对比 LLM vs 因子** | `python -m src.core.factor_backtest` | LLM 是否比纯因子组合更优 |
| **补充历史数据** | `scripts/backtest.py backfill` | 首次部署或融合 CSV 更新后执行 |

---

## 八、待改进

| 项目 | 优先级 | 说明 |
|------|--------|------|
| ly walk-forward 验证 | ✅ 已完成 | 当前为 expanding window walk-forward（每20天重训练）|
| AT 独立回测 | ✅ 已完成 | c1test Phase 4，TA JSON 日志 + stock_daily JOIN |
| ML sentiment 方向准确率 | ✅ 已完成 | c1test Phase 3，analysis_history + stock_daily JOIN |
| 模拟交易回测 | 🟡 中 | 当前只有方向准确率，缺 portfolio 模拟（仓位/止损/资金管理） |
| 更多历史数据 | 🟢 低 | 每日自动积累，1-2 月后 ML 20d 窗口才够用 |
| 贝叶斯 vs 线性模式对比 | 🟢 低 | 融合 JSON 中已有 dual 模式数据，但回测只用了 linear |
