# 'fusion' 行污染 analysis_history — ML 评估循环 / 口径失真 (2026-10-03)

> 类型: 数据口径缺陷 / 循环评估风险
> 影响: 所有"读 analysis_history 最新 sentiment 以评估/消费 ML"的点
> 处置: 读取端统一加 `report_type IN ('full','simple')` 防护; **不拆表**(见下)
> 状态: ✅ 已修复 (读取端)

## 现象

对 ML 子系统做 regime/准确率审计时发现: `analysis_history` 表中同一 `(code, day)` 同时存在
ML 子系统自身的分析行与**融合管线写回的行**, 二者被混用。

## 根因

`scripts/run_daily.py:419,460-468` 每日把**融合管线自身结果**写回 ML 的 `analysis_history`:

```python
sentiment_score = int(50 + fusion_score * discount * 16.67)   # 由融合分派生, 非 ML 输出
INSERT INTO analysis_history (... report_type='fusion' ...)
```

- 该行 `sentiment_score` 来自 **fusion_score**(三系统加权结果), 不是 ML 的 LLM 判断。
- 时间戳格式 `YYYY-MM-DDTHH:MM` (ML 自身行为 `YYYY-MM-DD HH:MM`); 字符串排序下 `'T'(0x54) > ' '(0x20)`,
  故朴素 `ORDER BY created_at DESC LIMIT 1` / `MAX(created_at)` **会优先选中 'fusion' 行**。
- 结果: "读取最新 ML sentiment" 实为"读取融合自身结果" → **循环评估**。

## 影响面 (实测)

- c1test `phase3_ml`: 去重后选中行 **65% 为 'fusion'** → ML 准确率被虚报 (49.9% vs 真值 46.5%)。
- 生产消费链: `src/loaders/mindlynx_loader.py`、`services/ml_factor_service.py`、`src/services/context_preparer.py`。
- 研究/校准: `research_skill_accuracy` / `calibrate_skill_scores` / `calibration_gap_report`
  / `diagnose_scoring` / `analyze_strategy_accuracy`。
- ML 子系统(fork): `src/services/realtime_monitor.py` / `src/core/llm_validation.py` / `src/core/auto_tune.py`。

## 处置 (读取端过滤, 2026-10-03)

所有 ML 评估/消费点统一加 `report_type IN ('full','simple')`:
- 生产链 3 处 + 研究脚本 5 处 + ML 子系统 3 处 (已记录 `docs/subsystem-fork-management.md`)。
- c1test `ml_prod` TEMP VIEW (去重 + 仅 ML 行); exp_ml_regime_alpha 同步。
- `stock_knowledge.py` 带 `@calibration 禁止修改` 标记, **未动**。

## 为何不拆表 (关键决策)

ML 前端 API `systems/MindLynx-Aistock/api/v1/endpoints/history.py:158-186` **有意**在
`analysis_history` 中展示融合行, 且 `CASE full→0 / fusion→1` **优先 full、退化时用 fusion**。
→ 把 'fusion' 行迁到独立表**会破坏 ML UI 的降级展示**。故正解是**读取端按用途过滤**,
不是写回端拆表。若未来确需拆表, 须先做专项论证 (c1skill) + 迁移 UI 消费方。

## 维护提示

- 新增 ML 报告类型时, 需同步上述过滤清单 (当前 DB 仅 `full/simple/fusion/market_review`,
  ML 行 = `full/simple`, 无遗漏)。
- 任何**新**的 `analysis_history` 读取点, 若用于评估/消费 ML, 必须过滤 `report_type`。

## 验证

- `pytest tests/` 166 passed; `py_compile` 全通过。
- 真实库 smoke: `mindlynx_loader._load_from_db("2026-09-30")` → 16 只正常。
- 修复后 ML (90天) fusion等效 46.5% (310/666), 全历史 T+1 47.9% (相对同混比基准 −2.0pp)。
