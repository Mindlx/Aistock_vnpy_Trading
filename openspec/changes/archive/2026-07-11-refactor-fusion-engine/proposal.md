## Why

`FusionEngine`（789 行）中包含两种融合策略（线性/贝叶斯）和辅助逻辑。`_fuse_linear` 圈复杂度 13、190 行；`_fuse_bayesian` 135 行。策略间复用 `_detect_disagreement` 等辅助方法，但耦合在单一类中，难以独立测试和扩展新融合模式。

## What Changes

1. 将 `_fuse_linear` 提取为 `LinearFusionStrategy` 类
2. 将 `_fuse_bayesian` + `_apply_bayesian_override` 提取为 `BayesianFusionStrategy` 类
3. 将分歧检测/权重调整等共享逻辑提取为独立函数
4. 将 `get_portfolio_summary` 提取为 `PortfolioSummarizer`
5. `FusionEngine` 保留为 Facade，委托策略类

## Capabilities

### New Capabilities
- `linear-fusion`: 线性加权融合策略
- `bayesian-fusion`: 贝叶斯概率融合策略
- `portfolio-summary`: 投资组合汇总

### Modified Capabilities
- 无（纯重构，不改接口）

## Impact

- `src/fusion_engine.py` — 减少约 500 行，保留 Facade
- `src/fusion/linear.py` — 新增
- `src/fusion/bayesian.py` — 新增
- `src/fusion/summarizer.py` — 新增
- `src/fusion/utils.py` — 新增（共享辅助函数）
