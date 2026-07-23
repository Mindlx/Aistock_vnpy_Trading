# 策略级准确率追踪与自动校准系统

> **状态**: 设计阶段  
> **最后更新**: 2026-07-23  
> **设计者**: c1skill 论证 + brainstorming

---

## 1. 问题

18 个策略 YAML 中仅 2 个有回测验证数据，其余 16 个的评分调整值（`sentiment_score +/- N`）均为未经检验的假设。系统运行时无法区分"哪个策略真的有效"。

## 2. 目标

建立一个自动闭环：**记录每个策略的使用情况 → 定期计算准确率 → 自动校准评分调整值 → 推送报告**。

## 3. 架构

```
                    ┌──────────────────────────────────┐
                    │         analysis_history         │
                    │  skill_id  sentiment_score  pct_chg│
                    └────────────────┬─────────────────┘
                                    │ 每日 20:30 (scheduler timer)
                                    ▼
                    ┌──────────────────────────────────┐
                    │       calibrate_skill_scores.py   │
                    │  1. 按 skill_id 聚合准确率       │
                    │  2. 低于 50% → 下调评分值         │
                    │  3. 高于 70% → 上调评分值         │
                    │  4. 输出校准报告                   │
                    └──────────┬───────────────┬────────┘
                               │               │
                               ▼               ▼
                    ┌──────────────┐   ┌──────────────┐
                    │ strategies/* │   │ WeCom 推送    │
                    │ .yaml 评分值  │   │ 校准报告      │
                    │ 自动更新      │   │              │
                    └──────────────┘   └──────────────┘
```

## 4. 组件

### 4.1 数据填充 — `pipeline.py`

**改动**：解析 `self.analysis_skills`，将激活的策略名填入 `analysis_history.skill_id`。

当前行为：
```python
skill_id=",".join(self.analysis_skills) if self.analysis_skills else "consensus"
```

目标行为：确保 `analysis_skills` 在生产路径中被填充。当前 `pipeline.py:111` 从 config 接收 `analysis_skills`，但调用方未传此参数。需在调用方（`analyzer.py` 或 `pipeline.py` 自身）中解析当前激活的技能列表。

**具体方案**：在 `pipeline.py` `_build_agent_message()` 中，将 `self.analysis_skills` 解析后持久化到 `analysis_history.skill_id`。

### 4.2 校准脚本 — `scripts/calibrate_skill_scores.py`

核心逻辑：

```python
for each skill_id in analysis_history:
    correct = count where sentiment_direction == actual_direction
    accuracy = correct / total
    
    if accuracy < 0.50:
        # 下调该策略所有正向评分调整值 by 1 notch
        # 例: +14→+10, +10→+8, +8→+5, +5→+3
    elif accuracy > 0.70:
        # 上调 by 1 notch
        # 例: +5→+8, +8→+10
    
    # 更新 YAML 文件中的评分调整值
    # 输出校准记录
```

校准规则：

| 当前准确率 | 操作 | 调整幅度 |
|:----------:|:----|:--------:|
| < 40% | 正向评分清零，仅保留负向 | — |
| 40-50% | 下调一档 | −2~−5 |
| 50-70% | 保持不变 | 0 |
| 70-80% | 上调一档 | +2~+5 |
| > 80% | 保持，打标"已验证" | 0 |

### 4.3 触发 — `scheduler.py`

复用现有 `diagnose-agreement` 的 20:30 timer：

```python
if hour == 20 and minute == 30:
    subprocess.run(["python", "scripts/calibrate_skill_scores.py"])
```

## 5. 数据流

```
LLM 分析 → analysis_history.skill_id 填入策略名
    ↓ 每日 20:30
calibrate_skill_scores.py 读取近 90 天记录
    ↓ 按 skill_id 聚合
计算每个策略的准确率
    ↓ 准确率 < 50% 或 > 70%
修改对应 strategies/*.yaml 的评分调整值
    ↓
输出校准报告到日志 + WeCom
```

## 6. 风险与缓解

| 风险 | 缓解 |
|:-----|:------|
| 样本量不足导致错误校准 | min_samples=30，低于此数的策略跳过 |
| 单次噪声导致评分大幅波动 | 调整幅度限一档，不跳级 |
| YAML 文件并发写入冲突 | 使用原子写入（写入临时文件 + rename） |
| `analysis_skills` 长期为空 | pipeline.py 添加日志告警 |

## 7. 实施计划

| 阶段 | 内容 | 预计改动量 |
|:----:|:-----|:----------:|
| Phase 1 | `pipeline.py` 填充 `analysis_skills` | 2 行 |
| Phase 2 | `scripts/calibrate_skill_scores.py` | ~150 行新文件 |
| Phase 3 | 集成到 scheduler 20:30 timer | 3 行 |
| Phase 4 | 观察 4 周，评估校准效果 | 无代码改动 |

---

## 版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-23 | v1 | 初始设计 |
