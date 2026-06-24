# L7 映射对齐性审阅 — c1skill + Oracle 联合论证

> 审阅日期: 2026-06-01
> 审阅对象: `src/normalizer.py` 三系统 L7 映射实现 vs `docs/research/semantic-alignment-analysis.md` 设计文档

---

## 执行摘要

实现与设计文档有三处独立偏差，导致 ly 在融合中被系统性压制：

1. L7 范围从 [-0.8, +0.8] 放大到 [-3, +3] 但未按比例调整映射值
2. ly 使用 logit+tanh 而非设计文档的分段线性映射
3. ml/at 查表值未按 3.75x 缩放比例调整

**修复方案：分段线性映射 + 三系统值统一校准，工作量约 1-4h。**

---

## Stage 0 — 原架构理解

### 设计文档的意图

来源: `docs/research/semantic-alignment-analysis.md` §2.4

```
L7 空间: [-0.8, +0.8]
映射方式: 分段线性（flat 中性区 + 锚点插值）
三系统对齐:
  S1(+0.80): ly≥75%,  ml=买入,  at=Buy
  S2(+0.55): ly≥65%,  ml=加仓,  at=Overweight
  S3(+0.30): ly≥55%,  ml=—,     at=—
  S4(0.00):  ly 45~55%, ml=持有/观望, at=Hold
  S5(-0.30): ly≥35%,  ml=减仓,  at=Underweight
  S6(-0.55): ly≥25%,  ml=卖出,  at=—
  S7(-0.80): ly<25%,  ml=—,     at=Sell
```

### 实际实现

来源: `src/normalizer.py`

```
L7 空间: [-3, +3]           ← 3.75x 放大（无一致性缩放补偿）
ly 映射: 3×tanh(logit(p)/2) ← S 曲线，非分段线性
ml 查表: 买入=+2.2, 加仓=+1.3, 持有=0.0, 观望=-0.1, 减仓=-1.7, 卖出=-2.1
at 查表: Buy=+2.3, Overweight=+1.3, Hold=0.0, Underweight=-1.3, Sell=-2.3
```

### 设计意图判定

实现是**偶然偏差（历史遗留）**，非刻意设计。三个改动各自独立做出，未做一致性补偿。

---

## Stage 1 — 量化偏差

| 等级 | 设计目标(×3.75) | 当前 ly | ly 偏差 | 当前 ml | 当前 at |
|------|----------------|---------|---------|---------|---------|
| S1 强烈看多 | +3.00 | +1.50 | **-1.50** | +2.2 | +2.3 |
| S2 看多 | +2.06 | +0.90 | **-1.16** | +1.3 | +1.3 |
| S3 谨慎看多 | +1.13 | +0.30 | **-0.83** | — | — |
| S4 中性 | 0.00 | 0.00 | 0.00 | 0.0 | 0.0 |
| S5 谨慎看空 | -1.13 | -0.90 | +0.23 | -1.7 | -1.3 |
| S6 看空 | -2.06 | -1.50 | +0.56 | -2.1 | — |
| S7 强烈看空 | -3.00 | -2.10 | +0.90 | — | -2.3 |

**ly 在所有非中性等级上系统性偏低，偏差 -0.83 ~ -1.50。**

---

## Stage 2 — 代码级证据

### 2.1 ly 当前映射公式

```python
# normalizer.py:194-195
logit_val = cls._logit(p)
score = 3.0 * math.tanh(logit_val / 2.0)
```

分母 `2.0` 控制 S 曲线斜率。分母越小曲线越陡。

### 2.2 推荐的分段线性映射

```python
# prob_up 锚点 → L7 目标值（设计目标 ×3.75 缩放）
ANCHORS = [
    (25, -2.06),   # S6 看空
    (35, -1.13),   # S5 谨慎看空
    (45, 0.00),    # S4 中性边界
    (55, 0.00),    # S4 中性边界（flat zone）
    (65, 2.06),    # S2 看多
    (75, 3.00),    # S1 强烈看多
]
# 45~55% flat zone → L7=0
# 其他区间线性插值
# <25% 钳位 -3.00, >75% 钳位 +3.00
```

### 2.3 额外发现: probability_k 默认值不一致

```python
# normalizer.py:321  (v3.0 文档)
# v3.0: k 默认从 2.5 降至 1.0

# fusion_engine.py:63  (实际代码)
self.probability_k = rel_config.get("probability_k", 2.5)  # 仍默认 2.5
```

除非 `config/settings.yaml` 显式设置 `reliability.probability_k: 1.0`，否则 Bayesian 模式走的是 v2.x 参数。

---

## Stage 3 — 推荐修复方案

### 3.1 涉及文件

| 文件 | 改动 | 行数 |
|------|------|------|
| `src/normalizer.py` | 替换 `normalize_lynx()` + 更新 `ML_BASE` + `TRADINGAGENT_L7_MAP` | ~40 |
| `src/fusion_engine.py` | `probability_k` 默认值 `2.5` → `1.0` | 1 |
| `tests/test_fusion.py` | 更新 5 个硬编码断言值 | 5 |
| `config/settings.yaml` | 可选：显式设置 `probability_k` | 1 |

### 3.2 不涉及的文件

- 三个子系统代码（零侵入）
- `src/reliability.py`（`to_probability` 函数不变）
- `src/fusion_engine.py` L7_THRESHOLDS（+0.5/+1.5/+2.5 不变）
- `config/settings.yaml` 权重（0.30/0.40/0.30 不变）

### 3.3 效果验证

```
场景: 三系统同时看多 (ly prob_up=65%, ml=买入, at=Buy)
当前:  ly=0.90  ml=2.2  at=2.3 → 融合=1.84  (看多)
修正后: ly=2.06 ml=3.0  at=3.0 → 融合=2.72  (强烈看多)
         ly 贡献从 14.7% → 22.7%
```

---

## Stage 4 — 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 测试断言失败 | 高 | 低 | 更新 5 个值 |
| Bayesian 概率偏移 | 中 | 中 | `probability_k` 同步设为 1.0 |
| 用户不适应新分数 | 中 | 低 | 文档先行，观察反馈 |
| at Sell 从 -2.3 → -3.0 | 低 | 低 | 设计明确 Sell 在 S7 |

---

## Stage 5 — 结论

问题确认：实现与设计文档的三处不一致累积的技术债。**这不是"ly 天生保守"的设计决策，而是 bug。** 建议执行上述修复方案。

---

## 参考文档

- `docs/research/semantic-alignment-analysis.md` — 原语义对齐设计文档
- `docs/research/mapping-optimization-analysis.md` — 映射优化分析
- `docs/research/mapping-c1skill-review.md` — 首轮 c1skill 审阅
- Oracle session: `ses_17ca9b37affeWbkc3Rtse5Fz94`
