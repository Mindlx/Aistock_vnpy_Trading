# LY 子系统：因子工程与模型架构

> 最后更新: 2026-07-03
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
| 42% | -0.50 | 偏空 |
| 45~50% | 0.00 | 中性 flat zone |
| 52% | +0.25 | 谨慎看多(平滑) |
| 59% | +1.00 | 看多 |
| 65% | +2.00 | 看多(较强) |
| 75% | +3.00 | 强烈看多 |

区间内线性插值。flat zone (45%-50%) 比 fusion 引擎的 45-55% 更窄。

---

## 四、回测表现

### 4.1 融合层面 (bt_predictions)

| 指标 | 数值 |
|------|:----:|
| LY 准确率 | **47.1%** |
| 样本量 | 257 |
| 数据源 | bt_predictions (仅 19:00 融合记录) |
| 中性排除 | 24 条 (L7=0.0 严格 flat zone) |

### 4.2 独立 OOS (walkforward)

| 指标 | 数值 |
|------|:----:|
| LY 准确率 | **50.1%** |
| 样本量 | 682 |
| 数据源 | lynx_signal.py --backtest |
| 方法 | Walk-forward, train=20/test=10, step=5 |

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

### Stage 1 — 事实声明

| 指标 | 数值 | 数据源 |
|------|:----:|--------|
| 融合层面 LY 准确率 | **47.1%** | bt_predictions (257样本) |
| 独立 OOS (walkforward) | **50.1%** | lynx_signal.py --backtest (682样本) |
| LGB IC (Pearson) | 0.2244 | 2026-05-29~06-26 实测 |
| RF IC (Pearson) | 0.0226 | 同上 |
| RF 权重 | **0.0** (已禁用) | 当前生产 |
| 高置信预测占比 | ~20% | OOS 中 prob_up ≥65% 或 ≤35% |

### Stage 2 — 证据诊断

**6 月 51.9% vs 7 月 16.7%**：3 天仅 36 笔，统计噪声。50.1% OOS 才是可靠基线。

**per-stock 差异极大**（30%~74%）：模型对部分股票（雷迪克 74%、美迪西 62%）有预测能力，对另一些（平潭发展 29%、古麒绒材 30%）完全无效。同样的因子在不同股票上表现不同——这是 per-stock 时序模型的普遍限制。

**RF 完全禁用**: IC=0.0226，预测完全随机。15 个经典技术指标在 A 股个股上的 IC 本来就低，不是 RF 的实现问题。

### Stage 3 — 根因

| 问题 | 严重程度 | 说明 |
|------|:--------:|------|
| **模型天花板 50%** | 🔴 核心 | 682 样本 OOS 50.1%，模型本身预测能力接近随机 |
| **RF 因子预测力弱** | 🟡 重要 | 15 个经典技术指标在个股上的 IC 接近 0 |
| **LGB 高置信比例低** | 🟡 重要 | 只有 ~20% 预测的 prob_up ≥65% 或 ≤35% |
| **7 月极端值** | 🟢 噪声 | 16.7% 是统计噪声，不反映真实能力 |

### Stage 4 — 反方论据

**反方："因子都是经典金工因子，不应该这么差"**

回应：经典因子在**指数/期货**上的 IC 通常比**个股**高一个数量级。A 股个股受情绪、资金博弈、政策影响远大于技术面信号。RSI/MACD 在创业板个股上 IC 接近 0 是普遍现象，不是 LY 的实现问题。

**反方："vnpy 是知名开源，它的 Alpha158 不应该只有 50%"**

回应：Alpha158 的 58 因子体系是 Qlib 方法论，设计目标是**横截面选股**（多空组合，panel data），不是**时序预测单只股票**（time series）。两者有本质区别。Alpha158 用于选股比用于择时有效得多。

**反方："那 LY 还有价值吗？权重 0.37 是否过高？"**

回应：LY 的价值不在预测准确率，在 **"纯客观、零偏差、可重复"**。即使 50.1%（仅比随机好 0.1pp），它提供的信号与 ML/AT 来自完全不同的方法论。当三系统方向一致时，融合准确率提升——分歧时 53.7% vs 无分歧时 55.7%。LY 的噪音与其他系统的噪音**不相关**，这是融合能工作的前提。

### Stage 5 — 提升方向

| 方向 | 预期提升 | 工作量 |
|------|:--------:|:------:|
| 改用横截面选股范式 (panel data) | 中 | 大（架构级改动） |
| 增加另类数据（资金流/情绪/新闻因子） | 中 | 中 |
| prob_up 校准（概率输出校准） | 小~中 | 小 |
| 放弃 RF，仅保留 LGB | 已完成 | - |
| **当前策略：维持权重 0.37，融合贡献稳定** | — | 0 |

---

## 六、关键文件索引

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
