# 回测系统资产全清单

> 最后更新: 2026-06-29
> 来源: 全代码库grep扫描 + systemd配置审计 + DB盘点
> 用途: `c1test` 统一回测编排器设计的输入基准

---

## 概要

项目共有 **3 大核心回测系统 + 10 个专用回测/诊断脚本 + 8 个 ML 子系统回测模块 + 10 个相关 systemd 定时器**。分为以下 8 类。

---

## 1️⃣ 三大核心回测系统

| 系统 | 入口 | 数据库 | 数据量 | 评估对象 | 自动执行 |
|------|------|--------|--------|---------|---------|
| **融合回测** | `scripts/backtest.py` (1394行) | `data/backtest/bt_results.db` (92KB) | 309预测/284匹配 | L7方向判定 | `scripts/c1test.py` Phase 1 (每日20:00) |
| **LY独立回测** | `lynx_signal.py --backtest` | Sina API + parquet缓存 | 682 OOS样本 | RF上涨概率方向 | `lynx-backtest.timer` 每周日10:00 |
| **ML独立回测** | `main.py --backtest` (8模块) | `stock_analysis.db` (32MB) | 1004评估 | operation_advice方向+盈亏 | pipeline 每日 |

### 1.1 融合回测详情

- **命令**: `.venv/bin/python scripts/backtest.py {init\|record\|check\|update\|report\|backfill\|simulate\|walkforward\|weight_sweep}`
- **数据流**: `fusion_engine.py → fusion_{date}.csv → backtest.py record → bt_predictions DB → check (unified_cache) → report`
- **DB路径**: `data/backtest/bt_results.db`
- **指标**: 方向准确率(融合/ly/ml/at)、分歧场景分析、信号分布、逐日趋势、个股统计、融合vs最优单系统
- **最新结果(2026-06-29)**: 融合57.7%, ML 62.3%, LY 53.0%, AT 53.9%

### 1.2 LY独立回测详情

- **命令**: `.venv/bin/python systems/lynx_vnpy/lynx_signal.py --backtest`
- **算法**: expanding window walk-forward (每20天重训练, 初始60天), 15技术特征, RandomForest
- **结果(682 OOS)**: 49.0% — 接近随机

### 1.3 ML独立回测详情

- **命令**: `cd systems/MindLynx-Aistock && .venv/bin/python main.py --backtest [--backtest-force\|--backtest-report\|--backtest-code CODE\|--backtest-days N]`
- **8子模块**: BacktestEngine, BacktestService, BacktestRepository, BacktestReport, FactorBacktest, WalkForward, PerformanceAnalyzers, CostModel
- **DB**: `systems/MindLynx-Aistock/data/stock_analysis.db`
- **表**: `backtest_results` (30列), `backtest_summaries` (26列)
- **评估窗口**: 5/10/20 交易日
- **最新(1004评估, 10d)**: operation_advice方向22.06% ⚠️（text路径）
- **关键盲区**: sentiment_score方向62.3% **未纳入ML独立回测** — 只在融合回测中可见

---

## 2️⃣ ML子系统回测模块 (8个核心 + 3个相关)

| 模块 | 路径 | 职责 |
|------|------|------|
| BacktestEngine | `.../core/backtest_engine.py` | 方向判定、止盈止损评估 |
| BacktestReport | `.../core/backtest_report.py` | 30+指标、NAV曲线(PNG) |
| BacktestService | `.../services/backtest_service.py` | 编排层、保存、报告 |
| BacktestRepository | `.../repositories/backtest_repo.py` | DB CRUD、候选筛选 |
| FactorBacktest | `.../core/factor_backtest.py` | 纯因子回测基线 |
| WalkForward | `.../core/walk_forward.py` | 过拟合检测 |
| PerformanceAnalyzers | `.../core/perf_analyzers.py` | Sharpe/Sortino/Calmar |
| CostModel | `.../core/cost_model.py` | 印花税、滑点 |
| *factor_recalibrate.py* | `.../core/factor_recalibrate.py` | 因子重校准 |
| *backtest_tools.py* | `.../agent/tools/backtest_tools.py` | Agent集成 |
| *memory.py* | `.../agent/memory.py` | 技能加权(消费回测数据) |

---

## 3️⃣ 专用回测/诊断脚本 (`scripts/`)

| 脚本 | 行数 | 用途 | 触发方式 |
|------|:----:|------|---------|
| `backtest_fusion.py` | ~100 | walk-forward历史回填 + prob_up日志回填 | 按需手动 |
| `backtest_lgb.py` | ~150 | LGB模型专项回测 (unified_cache数据) | 按需手动 |
| `factor_backtest.py` | ~30 | 纯因子回测包装器 (调用ML子系统) | 按需手动 |
| `backfill_prob_up.py` | ~80 | prob_up历史回填 (从fusion JSON) | 一次性 |
| `calibrate_alphas.py` | ~150 | per-stock alpha校准 | `calibrate-alphas.timer` 每日12:30 |
| `diagnose_agreement.py` | ~200 | LY+ML同向诊断数据积累 | `diagnose-agreement.timer` 每日20:30 |
| `at_significance.py` | ~100 | AT统计显著性 + 误差分析 | 按需手动 |
| `diagnose_rf_model.py` | ~200 | RF模型退化诊断 | 按需手动 |
| `measure_dual_model_ic.py` | ~200 | 双模型IC测量 | 按需手动 |
| `compare_rf_models_detail.py` | ~200 | RF模型详细对比 | 按需手动 |

---

## 4️⃣ 阈值校准脚本 (`scripts/`)

| 脚本 | 用途 | 自动执行 |
|------|------|---------|
| `calibrate_eastmoney_thresholds.py` | 东方财富评级阈值校准 | `eastmoney-calibrate.timer` 每月1日10:00 |
| `analyze_eastmoney_thresholds.py` | 东方财富阈值分析 | 按需 |
| `research_eastmoney.py` | 东方财富数据研究 | 按需 |
| `research_factor.py` | 因子研究 | 按需 |
| `calibration_gap_report.py` | 校准差距追踪 | 按需 |

---

## 5️⃣ 相关 systemd 定时器

| 定时器 | 时间 | 关联脚本 | 功能 |
|--------|------|---------|------|
| `fusion.timer` | 工作15:30→19:00 | `run_daily.py` | 日终融合→自动触发回测 |
| `lynx-backtest.timer` | 周日10:00 | `lynx_signal.py --backtest` | LY周回测 |
| `TA.timer` | 工作日16:00 | `run_daily.py --run-ta` | TradingAgent辩论 |
| `calibrate-alphas.timer` | 每日12:30 | `calibrate_alphas.py` | Alpha权重校准 |
| `diagnose-agreement.timer` | 每日20:30 | `diagnose_agreement.py` | LY+ML同向诊断 |
| `retrain-lgb.timer` | 工作日15:20 | `scripts/run_daily.py`内触发 | LGB+RF模型重训 |
| `eastmoney-rating.timer` | 09:53/13:53 | `scripts/run_daily.py`内触发 | 东方财富数据获取 |
| `eastmoney-calibrate.timer` | 每月1日10:00 | `calibrate_eastmoney_thresholds.py` | 阈值校准 |
| `ic-monitor.timer` | — | systemd service | IC监测 |
| `trace-collect.timer` | — | `trace_auto_collect.py` | c1skill轨迹采集 |

---

## 6️⃣ 已知盲区（待 c1test 修复）

| 盲区 | 根因 | 影响 | 修复Phase |
|------|------|------|:---------:|
| ❌ AT独立回测不存在 | AT只在融合层面评估 | 无法独立诊断AT退化 | Phase 3 |
| ❌ ML独立回测用operation_advice(22%) | 未纳入sentiment_score(62%) | 严重低估ML真实能力 | Phase 2 |
| ❌ 无统一报告格式 | 三套独立系统各自输出 | 无法横向对比 | Phase 1 |
| ❌ 无变化检测 | 每次回测结果独立 | 无法发现退化趋势 | Phase 2 |

---

## 7️⃣ 数据/报告存储

| 路径 | 大小 | 内容 |
|------|:----:|------|
| `data/backtest/bt_results.db` | 92KB | 融合回测 (309条) |
| `systems/.../stock_analysis.db` | 32MB | ML回测 (1004评估) |
| `data/c1test/` | ❌ 不存在 | c1test目标目录 |
| `data/research/eastmoney_snapshot/` | 2.6MB | 东方财富快照 (4份) |
| `data/traces/` | 168KB | c1skill轨迹 (44条) |

---

## 8️⃣ 子系统原生回测脚本（审计结论）

| 位置 | 文件 | 审计结论 |
|------|------|---------|
| `systems/lynx_vnpy/vnpy_bridge/ly_backtest.py` (157行) | **vnpy BacktestingEngine 回测** — 资本模拟/基准对比/图表，比 `lynx_signal.py --backtest` 更全面但依赖完整 vnpy 栈 | ⏳ **待集成** — 数据已就绪(`scripts/gen_vnpy_parquet.py` 生成 13 只 parquet)，但运行时缺 alphalens 等依赖，需独立 venv |
| `systems/lynx_vnpy/vnpy_bridge/run_alpha_pipeline.py` | Alpha因子pipeline | 🟢 已被 lynx_signal 替代 |
| `systems/lynx_vnpy/lynx_vnpy/alpha/strategy/backtesting.py` | vnpy原生回测引擎（上游代码） | 🟢 融合层不使用 |
| `systems/MindLynx-Aistock/src/core/factor_recalibrate.py` | 因子重校准（已纳入 pipeline） | ✅ 已集成 |
| `systems/MindLynx-Aistock/scripts/generate_rating_report.py` | 评级报告生成 | 🟢 回测报告辅助 |
| `systems/mind_TradingAgent/mind_tradingagent/dataflows/warehouse.py` | AT数据仓库(vendor集成) | 🟢 生产使用中 |
| `systems/lynx_vnpy/vnpy_bridge/data_converter.py` | 原 parquet 数据生成器 | ❌ 被 `scripts/gen_vnpy_parquet.py` 替代（依赖冲突，alphalens 不可安装） |

### 8.1 vnpy 桥接回测（ly_backtest.py）集成状态

**对比 lynx_signal.py --backtest vs ly_backtest.py**:

| 能力 | lynx_signal.py | ly_backtest.py | 说明 |
|------|:-------------:|:--------------:|------|
| 滑窗RF预测 | ✅ | ❌ | 只能用简单MA信号 |
| 资本模拟(资金/仓位) | ❌ | ✅ | vnpy BacktestingEngine |
| 基准对比(超额收益) | ❌ | ✅ | `--benchmark` |
| 绩效图表 | ❌ | ✅ | `--chart` → Plotly |
| 胜率/盈亏比 | ⚠️ 方向准 | ✅ 真实交易统计 | 含交易成本 |
| 数据依赖 | Sina API | parquet文件 | **数据已生成** |

**下一步**: 在 c1test Phase 3 中纳入 ly_backtest.py，需先解决 vnpy 依赖安装问题。

---

## 附录: 文档索引

- `docs/testing/backtest.md` — 回测系统详细设计文档 (2026-06-06)
- `docs/subsystems/ml/backtest.md` — ML回测子体系 (2026-06-06)
- `docs/changelog/2026-06-28_ic-measurement-dual-model.md` — 最近回测相关变更
- `docs/decisions/accuracy-calibrated-mapping.md` — v4.0映射论证
- `config/settings.yaml:111-114` — 回测配置段
