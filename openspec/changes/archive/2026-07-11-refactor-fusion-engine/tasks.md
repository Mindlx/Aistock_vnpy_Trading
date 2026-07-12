## 1. 拆分融合引擎

- [x] 1.1 创建 `src/fusion/` 包 + `utils.py`（共享函数）
- [x] 1.2 提取 LinearFusionStrategy → `src/fusion/linear.py`
- [x] 1.3 提取 BayesianFusionStrategy → `src/fusion/bayesian.py`
- [x] 1.4 提取 PortfolioSummarizer → `src/fusion/summarizer.py`
- [x] 1.5 FusionEngine 方法委托 + 删除重复代码
- [x] 1.6 修复 `test_fusion.py` 中 `_detect_disagreement` 解包
- [ ] 1.7 提交并归档
