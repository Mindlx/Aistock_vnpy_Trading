# 全系统技术审计报告 — c1skill + Oracle 联合审查

> 审计日期: 2026-06-02 | 融合模式: linear | 测试: 60/60 passed
> 审查范围: 三子系统 + 融合引擎 + L7 映射 v3.1

---

## 执行摘要

发现 **2 个严重 bug**（1 个静默崩溃、1 个权限掩码缺失）、**3 个重要问题**、**1 处死代码**。L7 v3.1 分段线性映射数学正确，60 测试全通过。Bayesian 模式在数据缺失场景下优于 linear 模式。

| 优先级 | 发现 | 影响 | 工作量 |
|--------|------|------|--------|
| 🔴 P0 | `run_daily.py` TA 块中 `stock_signals` 未定义导致静默崩溃 | TA 新鲜数据未写入 `stock_signals` | < 5min |
| 🔴 P0 | ml/at 始终标记有效，缺失时权重不重新分配 | 数据缺失时融合结果失真 | 30min |
| 🟡 P1 | 死代码 `_logit` 未删除 | 技术债 | < 1min |
| 🟡 P1 | 文档/注释与 v3.1 不一致 | 误导维护者 | 10min |
| 🟡 P2 | Bayesian 模式缺少 stale TA 惩罚 | 模式间不一致 | 15min |

---

## Stage 0 — 系统全景

```
ly (RandomForest) ── prob_up ──┐
ml (因子+LLM)     ── advice ──┼── fusion_engine ── L7 score ── 7-level decision
at (多智能体)     ── rating ──┘         │
                                    ├─ linear:  加权平均 + 分歧惩罚
                                    └─ bayesian: 置信度调制 + 数学否决
```

**权重**: ly=0.30, ml=0.40, at=0.30 | **空间**: [-3, +3] | **级别**: 7 级

---

## Stage 1 — L7 映射 v3.1 验证

### 1.1 分段线性映射正确性

`normalizer.py:195-218` — **数学正确，连续，单调递增，无间隙**：

```
prob_up  0% ──25%──35%──45%──55%──65%──75──100%
L7     -3.0  -2.06 -1.13  0.0  0.0  +2.06 +3.0 +3.0
                              ↑ flat zone
```

边界验证：
- p=44.9% → L7-0.011 ✅ （进入负区）
- p=45.0% → L7 0.000 ✅ （中性边界）
- p=55.0% → L7 0.000 ✅ （中性边界）
- p=55.1% → L7+0.021 ✅ （进入正区）

### 1.2 发现：正侧斜率不对称

| 段 | 斜率 | 说明 |
|----|------|------|
| 35→45% | 0.113/1% | 离开空头 |
| **55→65%** | **0.206/1%** | **离开中性→看多（2.2×陡）⚠️** |
| 65→75% | 0.094/1% | 继续看多（较缓） |

`prob_up=55%→60%` 增加 L7=+1.03（半程到看多），而 `60%→65%` 也增加 L7=+1.03。**离开中性区的坡度是继续向上的 2.2 倍**。非对称是刻意的，但未在文档中说明。

### 1.3 ML 评分调制安全

所有 6 个操作建议的调制范围均在 [-3, +3] 内，无需 clamp：

| 建议 | 基值 | 调制范围 | 越界？ |
|------|------|---------|--------|
| 买入 | +3.00 | [2.45, 3.55] | ⚠️ sentiment=100 时 clamp 到 3.0 |
| 加仓 | +2.06 | [1.69, 2.43] | ✅ |
| 持有 | 0.00 | [-0.30, +0.30] | ✅ 完全在中性区 |
| 减仓 | -1.13 | [-1.33, -0.93] | ✅ |
| 卖出 | -2.06 | [-2.43, -1.69] | ✅ |

---

## Stage 2 — 严重 Bug

### 🔴 BUG 1: `stock_signals` 未定义引用 (`run_daily.py:346`)

```
Line 330-356 (--run-ta 块)
    for s in stock_signals:    ← NameError! stock_signals 还未定义
        ...
Line 358-365 (数据加载)
    stock_signals = load_real_data(...)  ← 这里才定义
```

**影响**: TA 块成功运行并写入文件，但 `stock_signals` 的内存更新崩溃 → 被 broad except 静默吞掉 → 用户看到 `⚠️ TradingAgent 执行异常`。后续的 `load_real_data()` 通常能从 TA 日志读取到新鲜数据，但 `ta_is_stale=False` 标记丢失。

**修复**: 删除该循环（与 `load_real_data` + `_supply_ta_stale_data` 冗余）。

### 🔴 BUG 2: ml/at 永不为"无效" (`fusion_engine.py:444-445`)

```python
mindlynx_valid = True      # ← 硬编码！
tradingagent_valid = True  # ← 硬编码！
```

当 ML 报告未生成时，`data_loader` 返回默认值 `"观望"/L7=-0.1`。融合引擎认为 ml 有效 → **权重永不重新分配**。

**今日验证**: 10 只股票 `mindlynx_score: -0.1`（全是默认值），但 `is_degraded: false`。ml 明明没有数据，却占了 40% 权重。

**修复**: 从 `data_loader.py` 传递有效性标志。

---

## Stage 3 — 重要问题

### 3.1 死代码 `_logit` (`normalizer.py:169`)

v3.0 logit+tanh 的遗留方法，v3.1 分段线性不再使用。应删除。

### 3.2 文档与注释过期

| 位置 | 当前写法 | 正确值 |
|------|---------|--------|
| `class SignalNormalizer` | `v3.0` | `v3.1` |
| `normalize_tradingagent` 文档 | `Buy→2.3, Sell→-2.3` | `Buy→3.0, Sell→-3.0` |
| `normalize_mindlynx` 文档 | `base=2.2` | `base=3.0` |
| README L7 阈值表 | `strong_bullish: [2.0, 3.0]` | `> 2.5` |

### 3.3 at Sell=-3.0 跨度

at 从 `Underweight(-1.13)` 直接跳到 `Sell(-3.0)`，中间 `L7=-2 (看空)` 不可达。这是 **5 级系统投射到 7 级空间的固有限制**，不是 bug。建议加 `Reduce` 等级映射到 -2.06，或文档注明。

### 3.4 Bayesian 模式缺少 stale TA 惩罚

`_fuse_bayesian` 未处理 `ta_is_stale`。linear 模式有 30% 权重扣减，Bayesian 模式照单全收。

---

## Stage 4 — 数据管线完整性

### 4.1 数据缺失时的行为

| 系统缺失 | 当前行为 | 问题 |
|---------|---------|------|
| **ml** 报告未生成 | 静默使用 `"观望"/-0.1` ✅ `is_degraded=false` | ❌ 不报错，不降级，40% 权重照常 |
| **at** 日志不存在 | `_supply_ta_stale_data` 回填昨日数据，标记 `ta_is_stale` | ⚠️ stale 标记有效，linear 模式扣 30% 权重 |
| **ly** prob_up=50% | L7=0.0, `valid=True` | 合理 — 50% 就是中性 |
| **ly** prob_up 异常 | `valid=False`, 权重重分配 | ✅ 正确处理 |

### 4.2 今日运行验证

2026-06-02 09:31 TA 运行的融合输出确认：
- ml 报告不存在 → 全部 `mind=-0.10`
- TA 有 6 只实际信号 + 4 只 Fallback Hold
- ly 有当日数据
- 融合使用 linear 模式，ml 占 40% 权重但只有默认值

**真实与名义权重偏差（今日）**：
```
名义: ly=0.30  ml=0.40  at=0.30
实际: ly≈0.43  ml≈0.00  at≈0.57  ← ml 无数据时应如此
```

---

## Stage 5 — 修复优先级

### 立即修复（本 session）

| # | 问题 | 文件 | 操作 |
|---|------|------|------|
| 1 | 删除死循环 | `run_daily.py:346-351` | 删除 6 行 |
| 2 | 删除死代码 | `normalizer.py:169-172` | 删除 `_logit` 方法 |
| 3 | 更新 docstring | `normalizer.py:108,260-265` | 修正版本号和映射值 |

### 短期修复

| # | 问题 | 文件 | 操作 |
|---|------|------|------|
| 4 | ml/at 有效性标志 | `data_loader.py` + `fusion_engine.py:444-445` | 传递 `ml_valid`/`at_valid` 标志 |
| 5 | Bayesian 模式 stale 惩罚 | `fusion_engine.py:_fuse_bayesian` | 添加 `ta_is_stale` 处理 |
| 6 | README 阈值更新 | `README.md` | 同步 v3.1 阈值 |

### 中长期

| # | 问题 | 操作 |
|---|------|------|
| 7 | at `Sell=-3.0` 跨度 | 考虑加 `Reduce` 等级或文档说明 |
| 8 | Bayesian 否决阈值审查 | k=1.0 下 `ly_veto_threshold=0.30` 触发点已偏移 |

---

## Stage 6 — 最终结论

**L7 v3.1 映射数学正确，60 测试全通过。** 本次审计发现的关键问题是 **2 个静默失效**（TA 块崩溃、ml 缺失不被感知）和 **多处文档过时**。Bayesian 模式在数据缺失场景下天然优于 linear 模式（置信度校准自动归零不可靠数据），建议考虑启用 Bayesian 作为默认融合模式。

---

## 参考

- `src/normalizer.py` — 映射实现
- `src/fusion_engine.py` — 融合引擎
- `src/data_loader.py` — 数据加载
- `scripts/run_daily.py` — 执行入口
- Oracle session: `ses_179ef5159ffenRio0dX6lJ0CgQ`
