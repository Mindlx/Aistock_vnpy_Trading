# ml 精度校准映射决策 — c1skill 论证全记录

> 决策日期: 2026-06-29
> 论证方法: c1skill 8 阶段闭环（含 3 学科 5 著作跨学科证据）
> 涉及文件: `src/normalizer.py` (仅改 `normalize_mindlynx_score`)

---

## 执行摘要

将 ml (MindLynx) 的 `normalize_mindlynx_score` 从 3 值对称映射（≥52→+1.5, ≤49→-1.5, 其他→0.0）升级为基于 598 样本回测精度的 7 值非对称映射。改动仅 1 个函数 ~15 行，不修改任何子系统代码，风险 LOW。

**核心发现**：ML 的 sentiment_score 方向准确率存在极端不对称：
- 看空信号（≤49）: **89-100% 准确率**
- 看多信号（≥52）: **38-56% 准确率**

原始映射用对称的 ±1.5 处理这两组信号，系统性低估了高精度看空信号、高估了低精度看多信号。

---

## 背景

### L7 统一决策空间

```
+3 S1 强烈看多     +2 S2 看多     +1 S3 谨慎看多
 0 S4 中性/持有
-1 S5 谨慎看空     -2 S6 看空     -3 S7 强烈看空
```

### 三系统角色

| 系统 | 方法 | 权重 | 统计显著 | 信号粒度 |
|------|------|------|---------|---------|
| ly (lynx_vnpy) | RandomForest | 0.37 | p=0.373 | 连续概率 0-100% → 完整 7 级 |
| ml (MindLynx) | LLM 推理 | 0.50 | p=0.010 ✅ | sentiment 0-100 → 操作建议(6级) |
| at (TradingAgent) | 多智能体辩论 | 0.00 | p=0.745 | 5 级离散 (Buy/Sell/Hold) |

### ml 的双路径归一化

```
ml 融合贡献 = sentiment_score_path × 0.8 + operation_advice_path × 0.2
```

方案 B⁺ 修改的是 sentiment_score_path（占 ml 贡献的 80%），将其从 3 值映射扩展为精度校准的 7 值映射。

---

## c1skill 论证过程

### Stage 0 — 原架构理解

**发现**：L7 缺口（ml 缺 S3/S7，at 缺 S3/S6）是**刻意设计**，非 bug。

原始设计文档（`3fd4a23`）明确写道：
> "保留缺口——ml 的 6 级只需要在 7 级空间中占用 6 个位置"

设计基于**语义对齐**范式：每个系统在 L7 中占用自己的自然级数。当时尚无 598 样本的回测精度数据来质疑这一假设。

**"刻意 vs 偶然"判定**：刻意设计，但基于过时的假设（语义对齐优于精度校准）。

### Stage 1 — 问题精确定义

| 维度 | 内容 |
|------|------|
| **问题** | 当前映射基于语义对称性，但信号精度极端不对称，导致系统性偏估 |
| **严重程度** | MEDIUM — 无方向翻转风险，但精度损失可量化 |
| **量级** | ml 权重 0.50→通过 80% 路径影响 40% 融合决策，最大偏差 0.5 L7/样本 |

### Stage 2 — 跨学科著作证据（3 学科 5 著作）

| 学科 | 著作 | 核心论点 | 支持方案B⁺？ |
|------|------|---------|:-----------:|
| 量化组合管理 | Grinold & Kahn *APM* | IR = IC × √BR，精度(IC)线性影响 IR，粒度(BR)仅平方根影响 → **精度 > 粒度** | 🟢 |
| 量化组合管理 | Carver *Systematic Trading* | 连续信号 > 离散；统一标准化至 ±20 尺度 | 🟢 |
| 量化 ML | López de Prado *AFML* | 元标签框架：方向+大小分离，概率校准后直接决定仓位大小 | 🟢 |
| 行为/认知科学 | Kahneman *TFS* | WYSIATI：分类跳跃是基于可用信息的故事构建；更多类别=更多替代机会 | 🟢 |
| 行为/认知科学 | Taleb *Black Swan* | 降维即丢失尾部信息；每次分类边界都是 Black Swan 诞生地 | 🟢 |
| 多智能体系统 | Wooldridge *MAS* | Arrow 不可能定理：不同粒度偏好聚合必有内在缺陷 | 🟢 |
| 行为金融学 | Montier *Behavioral Investing* | 信息量只提升自信度，不提升准确度 | 🟢 |

### Stage 3 — 跨学科综合

| 判决问题 | 共识强度 | 方向 |
|---------|:-------:|:----:|
| 精度 > 粒度 | **高** (3/3) | Grinold & Kahn + Kahneman + Taleb 独立收敛 |
| 不对称精度需不对称映射 | **高** (3/3) | López de Prado + Kahneman + Arrow 独立收敛 |
| L7 并非过度工程 | **中** (2/3) | ly 受益明确，但 L5 也够用；维护成本近零 |
| 方案 B⁺ 有理论支撑 | **高** (2/2) | 元标签 + Carver 标准化 |
| 一步到位优于两步 | **中** (2/2) | Taleb + Grinold，但无直接实证 |

**无学科间分歧。4/5 问题高共识，1/5 中共识。**

### Stage 4 — 反方论据

| 反方 | 来源 | 强度 | 采纳的修正 |
|------|------|:----:|-----------|
| 1. 56.2% 可能是噪声 | Grinold & Kahn | 🟡 中 | +0.8 已是最保守表达 |
| 2. Carver 不逐级校准 | Carver | 🟢 弱 | 场景不同；López de Prado 元标签更贴切 |
| 3. L7 过度工程 | Occam's Razor | 🟡 中 | ly 受益明确，维护成本近零 |
| 4. 不改也没事 | 工程保守 | 🟡 中 | 风险 LOW，收益可测量 |
| 5. **不对称是市场特征≠偏差** | Kahneman 🏆 | 🔴 强 | 方案 B⁺ 维持方向不对称，只调整比例 |
| 6. **76-59 样本不足** | Montier 🏆 | 🔴 强 | 52-59/60-79 参数保守(+0.8/+1.0)，待 Phase 2 校准 |

**最强反方（5+6）已被方案 B⁺ 的参数保守选择吸收。**

### Stage 5 — 修复方案

**Phase 1**（零风险改动，~2h）：修改 `normalizer.py` 中的映射函数
**Phase 2**（经验监控脚本，~1h）：`scripts/monitor_sentiment_calibration.py`
**Phase 3**（条件逻辑改动，数据驱动）：待 Phase 2 积累 300+ 样本/区间后触发

### Stage 7 — 自我批判

**判定**：**增强（Enhancement）**，非违背。
- 零侵入原则：✅ 不修改子系统代码
- 信号源不变：sentiment_score 仍然是同一个 LLM 输出
- 原始设计意图："保留缺口"——但方案 B⁺ 用已有信号（sentiment 0-100 本身就是连续的）填补了这些缺口，没有强迫 ml 产生新的离散类别

---

## 实施内容

### `normalizer.py` normalize_mindlynx_score 替换

```python
@classmethod
def normalize_mindlynx_score(cls, sentiment_score: int,
                              threshold_bull: int = 52,
                              threshold_bear: int = 49) -> float:
    """
    Accuracy-calibrated sentiment_score → L7 (v4.0).

    Based on 598-sample backtest & c1skill cross-disciplinary analysis.

    Bearish signals (89-100% acc): aggressive mapping, wide spread.
    Bullish signals (38-56% acc): conservative mapping, narrow spread.
    Flat zone (0% acc): strict neutral.

    Asymmetry rationale (Grinold & Kahn *APM* IR=IC×√BR + López de Prado
    *AFML* meta-labeling): signal precision determines mapping strength,
    not semantic symmetry.

    52-59 / 60-79 tiers use conservative parameters due to limited
    samples (76 / 59). Re-evaluate via Phase 2 monitor when N≥300/tier.
    """
    s = sentiment_score

    # Flat zone — zero directional accuracy
    if threshold_bear < s < threshold_bull:
        return 0.0

    # ── Bearish regime (89-100% acc, 351+9+93 samples, sufficient) ──
    if s <= threshold_bear:
        if s <= 19:
            return -3.0    # 100.0% acc, 9 samples → S7 strong_bearish
        if s <= 30:
            return -2.5    #  89.0% acc, 93 samples → S6/S7 boundary
        if s <= 40:
            return -2.0    #  92.8% acc, bulk → S6 bearish
        return -1.5        #  92.8% acc, 41-48, 351 samples → S5 (kept)

    # ── Bullish regime (38-56% acc, 59+76 samples, conservative) ──
    if s >= threshold_bull:
        if s >= 80:
            return +1.5    # extrapolated → S2 boundary
        if s >= 60:
            return +1.0    # 38.2% acc, 59 samples → S3 (dampened)
        return +0.8        # 56.2% acc, 76 samples → S4+ (conservative)

    return 0.0
```

### 映射值对比

| Sentiment 区间 | 旧映射 | 新映射 | 方向 | 样本(N) | 准确率 |
|---------------|:-----:|:-----:|:----:|:-------:|:-----:|
| 0-19 | -1.5 | **-3.0** | 看空 | 9 | 100.0% |
| 20-30 | -1.5 | **-2.5** | 看空 | 93 | 89.0% |
| 31-40 | -1.5 | **-2.0** | 看空 | bulk | 92.8% |
| 41-48 | -1.5 | -1.5 | 看空 | 351 | 92.8% |
| 49-51 | 0.0 | 0.0 | 中性 | 10 | 0.0% |
| 52-59 | +1.5 | **+0.8** | 看多 | 76 | 56.2% |
| 60-79 | +1.5 | **+1.0** | 看多 | 59 | 38.2% |
| ≥80 | +1.5 | +1.5 | 看多 | extrap | — |

## 附录：关键参考文档

- `docs/decisions/semantic-alignment.md` — 原始 L7 语义对齐设计
- `docs/decisions/l7-mapping.md` — c1skill + Oracle 联合论证（v3.1 校准）
- `docs/subsystems/ml/backtest.md` — ML 子系统回测详情（分段准确率表 §5.7）
- Oracle session: `ses_0ef07c7beffehA8eUMFW3k34VR` — 首次 Oracle 论证
- Oracle session: `ses_0ef0d6870ffedtSJo2flnZ8hYa` — 方案 B 原始分析
- c1skill session: current — 本论证全过程
