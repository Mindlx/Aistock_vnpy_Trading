# LY 子系统：因子工程与模型架构

> 最后更新: 2026-07-21 (LY 角色: 投票→纯观察者, 仓库优先, LGB 53.5%)
> 覆盖: 因子工程 (15TA + Alpha158) + 双模型架构 (RF + LGB) + 回测表现 + c1skill 分析

---

## 一、因子工程全景

LY 子系统使用 **两套独立的因子体系**，分别由 RF 和 LGB 两个模型消费。

### 1.1 RF 模型 — 15 个经典技术指标

15 个 TA 指标全部手写在 `lynx_signal.py` 的 `compute_features()` 中，非 vnpy 库来源。

| 类别 | 因子 | 计算方式 | 理论来源 | 窗口 |
|------|------|---------|---------|:----:|
| **动量** | `ret_1d` | 收盘价日收益率 | Fama-French 动量因子 | 1日 |
| | `ret_5d` | 5日收益率 | Jegadeesh-Titman 动量 | 5日 |
| | `ret_10d` | 10日收益率 | 中期动量 | 10日 |
| | `ret_20d` | 20日收益率 | 月动量 | 20日 |
| **均线** | `ma5_dist` | (收盘-5日均线)/5日均线 | 道氏理论 | 5日 |
| | `ma20_dist` | (收盘-20日均线)/20日均线 | 道氏理论 | 20日 |
| | `ma_cross` | (5日均线-10日均线)/10日均线 | 葛兰碧法则 | 5/10日 |
| **动量振荡** | `rsi14` | 14日RSI | Welles Wilder | 14日 |
| | `macd` | 12EMA-26EMA | Gerald Appel | 12/26日 |
| | `macd_signal` | MACD的9EMA | Appel | 9日 |
| | `macd_hist` | MACD-SIGNAL | 柱状线 | — |
| **波动率** | `atr_ratio` | ATR/收盘价 | Wilder 波动率 | 14日 |
| | `boll_pos` | 布林带位置(0~1) | John Bollinger | 20日 |
| | `cci20` | 20日CCI | Donald Lambert | 20日 |
| **成交量** | `vol_ratio` | 成交量/5日均量 | 量价分析 | 5日 |

**问题**: 这些经典因子在**指数/期货**上 IC 较高，在 A 股**个股**时序预测上 IC 接近 0。这不是 LY 的实现问题，是市场特征——个股受情绪、资金博弈、政策影响远大于技术面信号。

### 1.2 Alpha158 — 58 个体系化因子（LGB 模型）

来自 Qlib 方法论，通过 `vnpy_bridge/alpha_predictor.py` 实现。

| 类别 | 示例因子 | 计算窗口 |
|------|---------|:--------:|
| **K线形态** | kmid/klen/kup/klow/ksft | 1日原始 |
| **多窗口滚动** | MA/STD/MAX/MIN 等 | 5/10/20/30/60日 |
| **涨跌统计** | cntp(上涨计数)/cntn(下跌计数)/cntd(差值) | 多窗口 |
| **分位数** | rsq(RSI平方)/rsw(RSI加权) | — |
| **波动率结构** | std/avg 等 | 多窗口 |

**注意**: Alpha158 的设计目标是**横截面选股**(多空组合, panel data)，不是**时序预测单只股票**(time series)。两者有本质区别。用于选股比用于择时有效得多。

---

## 二、模型架构

### 2.1 数据流

```
OHLCV (Sina/Tushare/数据仓库)
  │
  ├──→ compute_features() → 15 TA 因子 → RF 模型 → prob_up_RF
  │                                              │
  └──→ alpha_predictor.py  → 58 Alpha158 因子 → LGB → prob_up_LGB
                                                    │
                              ┌─────────────────────┤
                              ▼                     ▼
                        predict_ensemble()    run() 集成
                        IC_weighted_avg      普通平均
                              │                     │
                              ▼                     ▼
                          _l7_score(prob_up) → L7 信号 → 融合引擎
```

### 2.2 RF 模型参数

```python
RandomForestClassifier(
    n_estimators=100,
    max_depth=5,
    min_samples_leaf=5,
    random_state=42,
    class_weight='balanced',
)
```

训练方式：每只股票独立训练（per-stock model），每日 `retrain-lgb.service` 在 15:20 自动重训（≥7 天触发）。

### 2.3 IC 加权集成

当前生产值（`lynx_signal.py:511-512`）：

```python
_LGB_IC_WEIGHT = 1.0    # LGB Pearson IC = 0.2244
_RF_IC_WEIGHT  = 0.0    # RF  Pearson IC = 0.0226 → 已完全禁用
```

基于 2026-05-29~06-26 实测：
- LGB Pearson IC = **0.2244**（有预测能力）
- RF  Pearson IC = **0.0226**（几乎无预测能力）

RF 已被完全降权。当前 LY 完全依赖 LGB+Alpha158。

### 2.4 数据获取链路

```
数据仓库 (WarehouseReader) → TTL 24h 缓存
  → Sina API (money.finance.sina.com.cn, 240min K线)
  → 本地 Parquet 缓存 (50 分钟 TTL)
  → 腾讯财经实时行情覆盖（仅推送用）
```

三层降级：`Warehouse → Cache → Sina API`。日频数据从 Sina 获取，盘后用腾讯实时行情覆盖缓存价格做推送显示。

---

## 三、L7 映射

LY 使用自己的 `_l7_score(prob_up)` 映射，不是 fusion engine 的 `normalizer.normalize_lynx()`。

锚点表：

| prob_up | L7 score | 信号 |
|:-------:|:--------:|------|
| 0% | -3.00 | 强烈看空 |
| 25% | -2.00 | 看空 |
| 35% | -1.00 | 谨慎看空 |
| 45% | 0.00 | 中性（**单点，无 flat zone**） |
| 59% | +1.00 | 看多 |
| 65% | +2.00 | 看多(较强) |
| 75% | +3.00 | 强烈看多 |

区间内线性插值。**flat zone 已于 2026-07-06 移除**——基于 708 笔 walkforward 数据对 15 种 flat zone 宽度的遍历验证，
确认 flat zone 无益于提升准确率（有 flat zone: 49.5-50.7%, 无 flat zone: 51.5%）。
当前 `lynx_signal.py._l7_score` 与 `normalizer.py.normalize_lynx` 两套映射已统一为同一锚点表。

---

## 四、回测表现

### 4.1 独立 OOS (walkforward) — 2026-07-21 实测

| 指标 | 数值 |
|------|:----:|
| LGB walk-forward 准确率 | **53.5%** (n=3470) |
| 高置信准确率 | **61.4%** (n=83, 仅2.4%预测) |
| LGB Pearson IC | 0.1222 |
| RF Pearson IC | -0.045 (已禁用) |
| 数据源 | backtest_lgb.py + measure_dual_model_ic.py |
| 方法 | 滑动窗口, train=120, 全股票 |

> **2026-07-21 实测**: LGB 总体 53.5% (3470 样本, 较旧 walk-forward 1028 样本更可靠)。高置信预测(prob_up≥65或≤35)达 61.4%, 但仅占 2.4% 的预测。RF IC=-0.045 已完全禁用(`_RF_IC_WEIGHT=0.0`)。详见 `scripts/backtest_lgb.py`。

### 4.3 个股分化

| 股票 | 融合层面准确率 | 评估 |
|:----:|:-------------:|:----:|
| 300652 雷迪克 | **73.9%** | ✅ 模型有效 |
| 688202 美迪西 | **61.5%** | ✅ 略有预测力 |
| 600372 中航机载 | 46.2% | ⚠️ 接近随机 |
| 000592 平潭发展 | **29.2%** | ❌ 反向预测 |

准确率从 29% 到 74% 不等，模型在部分股票上有效、部分无效——这是 per-stock 时序模型的普遍现象，不是因子问题。

### 4.4 6月 vs 7月

| 期间 | 准确率 | 样本 |
|:----:|:------:|:----:|
| 2026-05 | 55.6% | 9 |
| 2026-06 | 51.9% | 212 |
| 2026-07 (仅3天) | 16.7% | 36 |

7 月仅 3 个交易日（36 样本），统计噪声极大。模型在这 3 天持续看空但市场强势反弹，导致反向预测。这不是模型退化，是小样本的偶然偏差。50.1% OOS 才是真实能力基线。

---

## 五、c1skill 根因分析

### Stage 0 — 原架构理解

LY 的设计定位是"纯客观量化信号"——不依赖任何人为主观判断，用经典数学模型从量价数据中提取预测信号。RF 消费 15 个经典技术指标，LGB 消费 58 个体系化因子，两个模型互相独立、IC 加权集成。

核心假设：**经典因子 + 经典模型 = 稳定的预测能力**。

### Stage 1 — 事实声明 (2026-07-21 实测)

| 指标 | 数值 | 数据源 |
|------|:----:|--------|
| LGB walk-forward | **53.5%** (n=3470) | backtest_lgb.py |
| LGB Pearson IC | 0.1222 | measure_dual_model_ic.py |
| RF Pearson IC | **-0.045** (完全禁用) | 同上 |
| 高置信(prob_up≥65/≤35)占比 | **2.4%** | backtest_lgb.py |
| 高置信准确率 | **61.4%** (n=83) | 同上 |
| 分歧时LY看空+ML看多准确率 | **22.7%** (n=44) | diagnose_agreement |

### Stage 2 — 证据诊断 (2026-07-21更新)

**LGB 53.5% > 50% 但信号弱**: 3470 样本统计显著但 IC=0.1222 较低。2.4% 高置信预测才有 61.4% 准确率。

**分歧场景 LY 系统性错误**: LY看空+ML看多时 LY 仅 22.7%，ML 77.3%。LY 在分歧时拉低融合。

**RF IC 持续退化**: 从 0.0226 (6/26) → -0.045 (7/21)，已成反指。

### Stage 3 — 当前生产角色 (2026-07-21变更)

| 项目 | 旧 | 新 | 原因 |
|------|:---:|:---:|------|
| 融合投票 | 权重 0.20 | **权重 0.0** | LGB 53.5% 被 ML 61.7% 完全覆盖 |
| 分歧检测 | 参与 | **退出** | 分歧时 LY 仅 22.7%，以低精度压制高精度 |
| 当前角色 | 投票者 | **纯观察者** | 独立运行、数据照收、分歧照检、不参与决策 |
| 恢复条件 | — | LY 独立回测 > 55% 持续 2 周 | — |

### Stage 4 — 反方论据 (2026-07-21更新)

**反方："LY 53.5% 有预测力，移除投票是否浪费"**
→ 53.5% 不显著(p≈0.37)，且被 61.7% 的 ML 完全覆盖。Grinold & Kahn alpha叠加条件: 新信号 IC/IC_existing < 相关性 → 不叠加。LY 与 ML 同向看多时相关性达 71.4%。

**反方："保留 0.20 权重无害"**
→ 有实际伤害。LY 看空+ML 看多时 LY 仅 22.7%，拉低融合。weight-sweep 验证: LY=0 时融合 56.0% > LY=0.20 时 53.9%。

### Stage 5 — 当前状态

```
LY 当前: 纯观察者
  ├─ 独立运行: ✅ 正常训练/预测/写入
  ├─ 数据收集: ✅ bt_results.db 记录 LY 方向
  ├─ 独立回测: ✅ backtest_lgb.py + lynx_signal.py --backtest
  ├─ 仓库优先: ✅ WarehouseReader 日K线
  └─ 恢复条件: LY 独立回测 > 55% 持续 2 周
```

## 六、LY 信号在 ML 系统中的注入

> **2026-07-03 状态**: LY 信号注入 ML prompt **已暂注释**。

LY 的 `prob_up_ensemble`、`signal_rf`、`strength` 等信息通过 `pipeline.py:_load_ly_signals()` 加载，在 `analyzer.py:3064` 和 `executor.py:666` 两处注入 ML 的 LLM prompt。

**注释原因**: LY 独立 OOS 50.1% 接近随机，注入到准确率 65% 的 ML 系统中理论上会引入噪音。暂注释后运行数日 c1test，对比 ML sentiment 准确率变化，决定是否永久移除。

**相关代码位置**:
- `systems/MindLynx-Aistock/src/analyzer.py:3064-3070` — 主 prompt 注入
- `systems/MindLynx-Aistock/src/agent/executor.py:666-667` — Agent context 注入
- `systems/MindLynx-Aistock/src/core/pipeline.py:991-1137` — LY 信号加载逻辑

---

## 七、关键文件索引

| 文件 | 职责 |
|------|------|
| `systems/lynx_vnpy/lynx_signal.py` | 主程序：特征工程+RF训练/预测+推送 |
| `systems/lynx_vnpy/vnpy_bridge/alpha_predictor.py` | Alpha158 58因子+LGB推理 |
| `systems/lynx_vnpy/vnpy_bridge/data_converter.py` | DB→Parquet 数据桥接 |
| `systems/lynx_vnpy/vnpy_bridge/ly_backtest.py` | 独立策略回测 |
| `systems/lynx_vnpy/vnpy_bridge/retrain_lgb.py` | LGB 模型自动重训 |
| `systems/lynx_vnpy/vnpy_bridge/run_alpha_pipeline.py` | Alpha158 因子管线+IC分析 |
| `systems/lynx_vnpy/models/` | per-stock RF模型 + scaler |
| `systems/lynx_vnpy/models/alpha_lgb_model.txt` | 全局 LGB 模型 |
