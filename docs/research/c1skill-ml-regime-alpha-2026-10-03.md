# c1skill 论证：ML(MindLynx) 方向准确率的 regime beta 分解

> 触发: 用户质疑 "印象中 ML 子系统准确率应该更高" (c1test 2026-10-03 报告 ML 46.4%)
> 方法论: c1skill Stage 0-7 + 铁律#8 实测优先 / #11 多方法交叉 / #12 实验审计 / #13 采样独立性
> 结论: **46.4% 是市场 regime beta; 生产对齐口径下 ML 无正 alpha (T+1 47.9%, 相对同混比基准 −2.0pp);
>        不构成调整融合权重的依据 → HOLD 权重**
> 版本: 2026-10-03 v4 (含全部口径审计更正 — 见 Stage 1b)

---

## Stage 0 — 原架构分析 (含生产口径溯源)

**ML 三层架构**: 层1 14 因子引擎 (独立 50.1%) → 层2 18 策略 (LLM 驱动) → 层3 LLM 推理 → `sentiment_score` + `operation_advice`。

**生产融合如何消费 ML (权威链, 逐环节代码验证)**:
1. `src/loaders/mindlynx_loader.py:82-95` — 每日取 `analysis_history` 中 `MAX(created_at)` 每 code (排除 `code='MARKET'`), **无 report_type 过滤** (运行时点早于写 fusion 行, 故实际取 ML 子系统行)。
2. `src/fusion/linear.py:57` — `normalize_mindlynx_score(sentiment)` → **v5.0** L7 (定义在 `src/normalizer.py:239`)。
3. 融合 CSV → `backtest.py record/check` → bt_predictions。

**融合权重** (`config/settings.yaml`, 2026-07-21): `lynx=0 / mindlynx=0.65 / tradingagent=0.35`; 依据 "LGB 53.5% 被 ML 61.7% 覆盖" (测于 **2026Q2**)。

---

## Stage 1 — 问题定义

- c1test 报告 ML 46.4%; 历史 7/31 63.6% → 10/3 46.4%; docs 记 64.1% (7/06)。
- c1test Phase3 三口径限定 `created_at >= now-90天` (滚动窗口) → 高准确率期滚掉。
- 问题: 下降是 ML 退化 还是 regime beta?
- 严重度 HIGH (影响核心权重)。

---

## Stage 1b — 脚本审计与口径更正 (2026-10-03, 用户要求全面验证)

对 `c1test.py::phase3_ml` 与 `exp_ml_regime_alpha.py` 做逐环节审计, 发现**三处口径缺陷** (均已修复):

### 🔴 BUG-1: 盘中重复分析被计为独立样本
- 同一 (code, day) 多达 **29 条**分析 (09:03–22:00), 共享同一 T+1 收益, 不独立。
- 生产是去重的 (`mindlynx_loader.py` `MAX(created_at)` 每 code)。
- **修复**: `--dedup latest|first|none` (默认 latest); c1test 加 `ml_prod` TEMP VIEW。

### 🔴 BUG-2 (更隐蔽, 验证中发现): 'fusion' 行污染 = 用融合结果评估 ML
- `scripts/run_daily.py:419,460-468` 每日把**融合管线自身结果**写回 `analysis_history`,
  且 `sentiment_score = int(50 + fusion_score*discount*16.67)` — **由融合分派生, 非 ML 输出**;
  `report_type='fusion'`, 时间戳 `YYYY-MM-DDTHH:MM`。
- 字符串排序下 'T'(0x54) > ' '(0x20): 朴素 `MAX(created_at)` 会**优先选中 'fusion' 行** →
  去重后反而 65% 是融合结果 (循环评估)。
- 实测: 全部类型去重选中 1323 行中 858 行为 'fusion'。
- **修复**: `ml_prod` 限定 `report_type IN ('full','simple')` (ML 子系统行), 排除 'fusion'/'market_review'。

### 🔴 BUG-3: c1test 用**过时 v4.0 映射**, 与生产 v5.0 不一致
- `src/fusion/linear.py:57` 生产用 `normalize_mindlynx_score` (**v5.0**, 2026-07-27 校准);
  c1test 内嵌 `_normalize_v4` 仍是旧 v4.0 (≤19→-3.0 / 41-48→-1.5 / 52-59→+0.8 / s=49→中性)。
- 差异点: **s=49 在 v4 中性 / v5 看空**, 及若干幅度 (sign 多数一致)。
- **修复**: c1test 新增 `normalize_ml_sentiment_l7` (v5 复刻) + `TestMlL7Mapping`
  对 0..100 全值与 `SignalNormalizer.normalize_mindlynx_score` **逐值交叉校验** (防再漂移)。

### 影响量化 (T+1, 全历史去重口径)

| 口径 | n(有向) | ML 准确率 | Δ vs 同混比基准 |
|:--|:--:|:--:|:--:|
| 含盘中重复 (原) | 2309 | 54.8% | +1.9 |
| 去重 (仅 ML 行 + v5) | 755 | **47.9%** | **−2.0** |
| (去重, 但含 fusion 行 — 中间错误版本) | 1081 | 50.5% | +1.1 |

> 结论: 原报告的高值主要是**重复计数 + fusion 行污染**的假象。

### 🟡 其他验证结论
- `analysis_history.created_at` **无 compact 污染** (仅 hyphen+space / hyphen+T), `substr(...,1,10)` 与交易日 JOIN 安全。
- T+1 语义: `run_daily.py:537` `today` = 预测日; `backtest.py cmd_record/check` 取**下一交易日** → 已核对一致。
- AT 独立回测 (`phase4_at`) 原按 CSV 日期取**同日** pct_chg (预测前已发生) → 修正为下一交易日 (**AT 43.7%→49.9%**)。
- 子系统表原漏 AT 独立口径覆写 → 补上; 且 override 前移到 `detect_changes` 之前 (消除跨口径假告警)。
- 独立子代理审计: 无前向收益/桶计数 bug; 采样独立性已按日聚类报告。

---

## Stage 2 — 证据 (生产对齐口径: 仅 ML 行 + v5 + 去重)

**L1 框架**: Grinold & Kahn — 信号价值须相对基准 (IR); 净空头在下跌市的高命中率是 **beta**。决策证据以 **L2 实测** 为主 (铁律#8)。

**实验**: `scripts/exp_ml_regime_alpha.py` — T+1/5/20 (交易日) × 季度 × regime (分析日之前 20 交易日市场趋势, 无前视); 三基准在"有方向"子集。黄金参考 T+1 生产口径 **654/654 ✓**。raw 2501 → dedup 847 (code,day)。

### 总体

| 持有期 | ML | 恒定看多 | 恒定看空 | 同混比基准 | ML−混比 | 看多占比 |
|:--|:--:|:--:|:--:|:--:|:--:|:--:|
| T+1 | 47.9% (362/755) | 46.6% | 51.9% | 49.9% | **−2.0pp** | 37.6% |
| T+5 | 46.9% (326/695) | 44.6% | 55.4% | 51.4% | **−4.5pp** | 37.3% |
| T+20 | 46.4% (222/478) | 33.5% | 66.5% | 53.5% | **−7.1pp** | 39.3% |

### 按季度

| 季度 | T+1 ML | 混比基准 | Δ | 看多占比 | T+20 Δ |
|:--|:--:|:--:|:--:|:--:|:--:|
| 2026Q2 | **58.4%** (n=89) | 59.9% | **−1.5** | 14.6% | −4.9 |
| 2026Q3 | **46.5%** (n=666) | 49.4% | −2.9 | 40.7% | −9.9 |

> Q2 高准确率来自"市场在跌"(下跌率 85%) + ML 偏空 (85% 看空); **相对基准 Δ 为负 = 纯 beta**。

### 按 regime

| regime | T+1 ML | 混比基准 | Δ |
|:--|:--:|:--:|:--:|
| down | 46.0% | 47.9% | −1.9 |
| flat | 49.3% | 49.8% | −0.5 |
| up | 49.3% | 53.9% | −4.6 |

### 多空条件准确率

| 持有期 | 看多命中 | 看空命中 |
|:--|:--:|:--:|
| T+1 | 43.7% (n=284) | 50.5% (n=471) |
| T+5 | 38.6% | 51.8% |
| T+20 | **24.5%** | **60.7%** |

> ML 是**净空/防守型**: 看多命中随持有期恶化 (43.7%→24.5%), 看空命中上升 (50.5%→60.7%, 接近市场下跌率)。

---

## Stage 3 — 综合

1. **生产对齐口径下 ML 无正 alpha**: T+1 Δ=−2.0pp, T+5 −4.5, T+20 −7.1 — **所有 horizon/regime 的 Δ 全为负**。
2. **绝对准确率 ≈ 市场下跌率**: Q2 下跌率 85% → 58.4%; Q3 下跌率 ~51% → 46.5%。
3. **多持有期不救场** (Δ 随持有期更负)。
4. 报告的历史高值 (61.7/64.1%) 是 Q2 regime beta + 重复/fusion 污染。

---

## Stage 4 — 反方论据

- **反方 0 (方法完备)**: 多持有期 × 多时段 × regime 分层均覆盖。不足: 单池 (20 只)/单区间; T+20 独立窗口仅 4 → 幅度不可外推。
- **反方 0b (脚本正确)**: 黄金参考 T+1 生产口径 654/654 ✓; 子代理审计无计算 bug; **口径缺陷 BUG-1/2/3 已修复**。
- **反方 0c (统计完备)**: 同混比随机基线 + 按日聚类 + 跨期一致性。
- **反方论据 (ML 仍有价值)**: ML=0.65 初衷含"因子层之上 LLM 增值"; 小池不排除跨池翻正; 现 T+1 仅 −2.0pp 而非大幅负。
- **修正精度**: 不是"ML 无用", 而是 **"生产口径下 ML 无正 alpha (略负), 报告数字是 regime beta + 口径假象"** → 不构成调权依据。

---

## Stage 5/6/7 — 方案 / 实施 / 自检

| Phase | 方案 | 本次 |
|:--|:--|:--|
| P1 | 口径修复 + 文档标注 | ✅ |
| P2 | 非对称方案 (ML 只看空/降权看多): 需预注册决定性实验 | ⏸ |
| P3 | 直接调 ML 权重 | ❌ 否决 (regime timing/过拟合, 铁律#9) |

- **决策**: **HOLD** 权重 (lynx=0 / mindlynx=0.65 / tradingagent=0.35)。
- **c1test 修复**: `ml_prod` (去重+仅 ML 行) / v5 映射 / T+1 下一交易日 / AT 独立口径 + override 顺序。
- **Stage 7**: 补全了原权重决策缺失的 regime 校正**与口径校正**, 非违背架构。
- **遗留**: `docs/goose-doc/c1skill-backtest-validation.md` 称 fusion_equivalent = "sentiment×0.8 + advice×0.2", 与实际 (仅 sentiment→v5) 不符, 待订正。

复现:
```bash
.venv/bin/python scripts/exp_ml_regime_alpha.py                 # 生产对齐(默认)
.venv/bin/python scripts/exp_ml_regime_alpha.py --dedup none     # 对照: 含盘中重复
```

---

## 附: 关键数字速查 (生产对齐)

- ML T+1 **47.9%** (Δ vs 同混比 −2.0pp) / T+5 46.9% (−4.5) / T+20 46.4% (−7.1)
- Q2 T+1 58.4% (n=89) vs 混比 59.9% → **Δ−1.5 (纯 beta)**; Q3 46.5% vs 49.4% → −2.9
- 看多命中 43.7% → T+20 24.5%; 看空命中 50.5% → 60.7%
- c1test 90 天: ML fusion等效 **46.5%** (310/666) / sentiment 51.3% / AT 49.9%
- 含重复口径 ML T+1 54.8% → 重复计数假象
- 三处口径缺陷: 重复计数 / 'fusion' 行污染 / v4 映射过期
