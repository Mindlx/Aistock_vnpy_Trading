# D13: 高中间峰双底因子化

> **日期**: 2026-07-23  
> **状态**: 已实施  
> **涉及系统**: ML 因子引擎 + LLM Agent + 策略层

---

## 决策

将高中间峰双底变体作为第 13 个因子 `pattern_chart_elevated` 加入 `factor_engine.py`，同步在 LLM 路径（tool description 硬规则 + strategy YAML）和因子路径（数学权重 0.05）双路覆盖。

## 动机

- 回测验证 250 信号，高中间峰变体 20d 胜率 **84.4%**（avg+14.32%），p<0.001 vs 标准双底
- 与现有 12 因子的 Spearman 相关性 R²=**0.0006**——几乎完全独立的信息维度
- 自身 IC=+0.049（p<0.0001），独立预测力统计显著
- 在 A 股 200+ 只/12 个月数据中稳定重复

## 架构变更

| 变更 | 文件 |
|------|------|
| 新增第 13 因子 `pattern_chart_elevated` (weight=0.05) | `factor_engine.py` |
| `ml_factor_service` 数据源从 `stock_analysis.db`→`data_warehouse.db` | `ml_factor_service.py` |
| 新增策略 `elevated_double_bottom.yaml` (priority=25) | `strategies/` |
| 分析工具描述注入 84.4% 权重硬规则 | `analysis_tools.py` |
| `subtype_score` 标注 (1.0/2.0) 区分两种变体 | `analysis_tools.py` |
| `factor_monitor` 添加新因子跟踪 | `factor_monitor.py` |

## 信号路径

```
高中间峰双底 (一次检测, 三条路径并行):
  1. factor_engine (weight=0.05) → composite_score → ml_signal.json → fusion
  2. strategy YAML → LLM agent → sentiment_score → fusion
  3. tool description 硬规则 → LLM prompt 内嵌权重 → sentiment_score
```

## 相关文档

- `docs/research/double-bottom-variant-analysis.md` — 完整研究和回测记录
- `docs/architecture/current-state.md` — 系统状态快照

## 版本

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-23 | v1 | 初始决策 |
