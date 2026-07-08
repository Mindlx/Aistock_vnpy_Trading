# 回测口径对比分析：ML回测 vs 融合回测

> c1skill Stage 0-5 六阶段完整分析（2026-07-08）
> 议题：统一两条独立回测口径和标准

## 背景

系统中有两套独立的回测管线，对同一ML信号得出差异巨大的准确率数字（如某股ML报告sent_acc=100%，融合报告ml_acc=42.9%），同时动态alpha消费链错误地消费了ML口径数据，导致alpha系统性高估。

### 两条管线总览

| | ML回测 | 融合回测 |
|---|---|---|
| **代码** | `systems/MindLynx-Aistock/src/core/backtest_engine.py` | `scripts/backtest.py` |
| **数据库** | `stock_analysis.db → backtest_summaries` | `bt_results.db → bt_predictions` |
| **输出字段** | `sentiment_direction_accuracy_pct` | `ml_correct` / `ml_acc` |
| **消费者** | `reliability.py _alpha_from_db()` | c1test报告 / 每周cron推送 |
| **评价对象** | 原始 `sentiment_score` 的方向 | 混合信号 `ml_score` 的方向 |

---

## 维度1：评价的信号对象不同（最根本）

```
ML回测：
  只看 sentiment_score 的方向 (0-100整数)
  不含 operation_advice 的影响

融合回测：
  ml_score = sentiment_normalized×0.8 + advice_normalized×0.2
  混合后的L7得分 → _sign → 方向
  含 operation_advice 的影响
```

示例：sentiment=60（看多倾向）+ operation="减仓"（看空操作）
- ML回测：60≥52→"up"，报告"看多正确"
- 融合回测：normalized_score=+1.0×0.8 + (-1.13+mod)×0.2 ≈+0.6，轻微看多
- 两者评价的不是同一信号

---

## 维度2：评分映射方法

**ML回测（backtest_engine.py:147-163）**：
```
score >= 52 → "up"
score <= 49 → "down"
score 50-51 → "flat"
```
三元阈值分类，flat zone=50-51

**融合归一化映射（normalizer.py:259-316）**：
```
50-51 → 0.0 (严格中性)            52-59 → +0.8
60-79 → +1.0                      ≥80  → +1.5
41-48 → -1.5                     31-40 → -2.0
20-30 → -2.5                      ≤19  → -3.0
```
六段分段线性映射到[-3,+3] L7空间，flat zone也是50-51→0.0→_sign→中性

**一致性判断**：评分映射本身基本对齐，52-59区间ML标"up"、融合标+0.8→sign=+1，方向一致。40-48区间ML标"down"、融合标-1.5→sign=-1，方向也一致。**这不是差异的主要原因。**

---

## 维度3：评价窗口 ⚠️ 最主要的差异来源

**ML回测（backtest_engine.py evaluate_single + stock_repo.py get_forward_bars）**：
```
eval_window_days = 5 或 10
start_price = 分析日(T)收盘价
window_bars = forward_bars[:eval_days]  # T+1, T+2, ..., T+5（或T+10）
end_close = window_bars[-1].close       # T+5（或T+10）收盘价
stock_return_pct = (close_T+5 - close_T) / close_T × 100  # 累计5天/10天收益
含止损止盈模拟（hit_stop_loss / hit_take_profit, 在窗口内逐日扫描）
含模拟持仓收益（simulated_return_pct）
```

> 注意：ML回测评的是T+5（或T+10）的**累计涨跌幅**，不是T+1的次日涨跌幅。window_bars从 `date > analysis_date` 开始取，不含T日自身。

**融合回测（backtest.py:354-384）**：
```
T+1 次日
pct_chg = (next_close - pred_close) / pred_close × 100
无止损止盈模拟
```

**影响放大效果**：

假设持股5天，第1天+2%，4天微跌0.5%合计-2%，第5天涨0.3%，最终-1.7%：
- ML回测（5天窗口，方向=up）：return=-1.7%，在±2% band内 → **neutral，不纳入统计**
- 融合回测（T+1）：只看第1天（+2%）→ **correct**

这是两条口径数字差异的最大原因。不同窗口长度天然产生不同的正确率，且取决于该股票的短期波动特征。

---

## 维度4：return neutral band ⚠️ 第二大差异

**ML回测（_classify_outcome, backtest_engine.py:572-609）**：
```
"up"方向：return ≥ +2.0% → win(correct)
          return ≤ -2.0% → loss(incorrect)
          -2.0% < return < +2.0% → neutral (排除，不纳入分母)

"down"方向：return ≤ -2.0% → win(correct)
            return ≥ +2.0% → loss(incorrect)
            -2.0% < return < +2.0% → neutral (排除)

"flat"方向：|return| ≤ 2.0% → win(correct)  ← 注意flat有2%安全垫
            |return| > 2.0% → loss(incorrect)
```

**融合回测（backtest.py:354-356）**：
```python
actual_dir = 1 if pct_chg > 0 else (-1 if pct_chg < 0 else 0)
```
纯符号判断，无任何band。

**融合回测没有±2%安全垫**。对于T+1来说，微涨0.01%就算"涨"。这使得：
- 融合的看多准确率低于ML（因为ML的±2% band排除了大量模糊判断，只统计方向明确的样本）
- 融合的看空准确率也低于ML（同样的原因）
- 融合更容易被0.5%以内的随机波动影响

---

## 维度5：中性方向的安全垫设计

ML回测有个特殊设计：当sentiment在50-51（flat）时，`|return|≤2%` 被记为正确。这意味着：

> **当模型判断"无方向"且行情确实无方向（±2%内）时，被算作正确**

这会推高ML的sent_acc——LLM说"不确定"是正确的判断。但融合回测中，sentiment=50-51→0.0→sign=0→中性，中性预测排除不纳入统计。**两种"中性"的处理方式不同。**

---

## 维度6：operation_advice的参与度

| | ML回测 | 融合回测 |
|---|---|---|
| sentiment方向 | ✅ 独立评价（sentiment_direction_correct） | ✅ 混合在ml_score中 |
| op方向 | ✅ 独立评价（direction_correct） | ✅ 混合在ml_score中 |
| 两者混合评价 | ❌ 没有"混合后方向"指标 | ✅ ml_score就是混合后评分 |

ML回测有两条独立的准确率指标，融合回测只有一个混合后的ml_score。**这对应了消费侧的差异**：动态alpha应该消费哪一个？

---

## 维度7：综合对比总结

| 维度 | ML回测（已决定改造） | 融合回测（保持） | 结论 |
|------|--------|---------|:----:|
| 评价信号 | 原始sentiment_score | 混合后ml_score | 不同事物，保留差异 |
| 评分映射 | 52/49三元阈值 | 6段分段[-3,+3] | 🟢 基本对齐 |
| 评价窗口 | T+5累计/T+10累计 | T+1次日 | 🔴 **最大差异，待决策** |
| return band | ±2.0%中性区 | 纯符号判断 | 待定，见维度4 |
| flat方向处理 | \|return\|≤2%=正确 | 中性排除 | 🟡 需对齐 |
| op参与 | 两条独立指标 | 混合在ml_score中 | 🟡 各有用途 |

---

## 建议

### 待决策的差异

以下差异已经过c1skill分析，暂不做对齐决策，文档记录留作参考：

| 差异 | 当前状态 | 点评 |
|:----:|:--------:|------|
| **评价窗口** | ML:T+5累计/T+10累计 vs 融合:T+1 | 🔴 最大差异，待决策 |
| **return band** | ML:±2% vs 融合:纯符号 | 保留差异，各有价值 |
| **flat方向处理** | ML:\|return\|≤2%=正确 vs 融合:中性排除 | 保留差异，各有价值 |
| **op参与度** | ML:两条独立指标 vs 融合:混合ml_score | 保留差异，各有价值 |

### 建议

两套口径保留，名分明确，消费侧对齐：

1. **ML回测口径**（sentiment_direction_accuracy_pct）→ 用于LLM模型诊断/调prompt/换模型时的能力评估
2. **融合回测口径**（bt_results.ml_correct）→ 用于动态alpha校准/权重调整/系统级决策
3. **动态alpha切换数据源**：reliability._alpha_from_db() 在数据量充足后（500+样本）改为消费融合回测的ml_acc

---

## 参考资料

- ML回测引擎：`systems/MindLynx-Aistock/src/core/backtest_engine.py`
  - `infer_direction_from_score` (行144-163) — 阈值分类
  - `_classify_outcome` (行572-609) — 结果判定含±2%band
  - `_evaluate_single` (行214-...) — 窗口评价
  - `sentiment_direction_correct` 计算 (行269-272)
- 融合回测：`scripts/backtest.py`
  - `_sign` (行138-144) — 方向判断threshold=0
  - `_is_correct` (行146-156) — correct/wrong/neutral
  - T+1匹配 (行354-384)
- 归一化管线：`src/normalizer.py`
  - `normalize_mindlynx_score` (行259-316) — 6段映射
  - `normalize_mindlynx` (行217-254) — op建议归一化
- 动态alpha消费：`src/reliability.py`
  - `_alpha_from_db` (行60-110) — 当前读ML口径
  - `STOCK_ALPHA_OVERRIDE` (行44-54) — 静态回退
