# 策略级准确率追踪

> **状态**: 研究阶段 — 脚本已就绪，等待数据积累  
> **最后更新**: 2026-07-23

---

## 问题

18 个策略 YAML 中仅 2 个有回测验证数据（高中间峰、情绪周期），其余 16 个的评分调整值均为未经检验的假设。

## 现有基础设施

`analysis_history` 表已包含 `skill_id` 字段，每次 LLM 分析时记录激活的策略 ID（逗号分隔）。`backtest_summaries` 表也支持scope='skill' 按策略维度存储。

但当前 `analysis_skills` 字段在生产路径中未填充，导致 `skill_id` 默认存为 `"consensus"`。

## 脚本

`scripts/research_skill_accuracy.py`：从 `analysis_history` 解析 `skill_id`，按策略聚合准确率。

```bash
python scripts/research_skill_accuracy.py
```

## 前置条件

要让脚本输出有意义的按策略准确率，需要 `analysis_history.skill_id` 存入实际策略名称而非 `"consensus"`。这需要在 `pipeline.py` 初始化时传入 `analysis_skills` 参数。
