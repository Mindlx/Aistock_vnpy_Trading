# ML 子系统 + 融合系统改进路线图

> 最后更新: 2026-06-07
> 基于全量回测数据与 c1skill 论证

---

## 已完成的工作

| 领域 | 任务 | 状态 |  commit |
|------|------|------|---------|
| **回测基建** | sentiment_score 纳入回测评估 | ✅ | c6aa739 |
| | upsert_summary 缺失字段修复 | ✅ | c6aa739 |
| | factor_baseline 死代码激活 | ✅ | c6aa739 |
| | context_snapshot schema 统一 | ✅ | c6aa739 |
| **阈值校准** | 60/40 → 52/49 (覆盖 57%→98%) | ✅ | c6aa739 |
| | flat 降权 ×0.5 (normalizer.py) | ✅ | c6aa739 |
| | 阈值集中配置化 (settings.yaml) | ✅ | c6aa739 |
| **权重校准** | alpha 0.55 → 0.65 | ✅ | c6aa739 |
| | per-stock alpha 覆盖表 (7/10 只差异化) | ✅ | c6aa739 |
| | 动态 alpha 从 DB 读取 (缓存 TTL=1h) | ✅ | c6aa739 |
| | auto-calibration 脚本 (calibrate_alphas.py) | ✅ | c6aa739 |
| **信号路径** | HP3: sentiment_score 独立融合路径 | ✅ | c6aa739 |
| **提示词校准** | SCORING_CRITERIA + ACTION_GUARDRAILS v2 | ✅ | c6aa739 |
| | 15 策略 YAML 添加理论家名字引用 | ✅ | c6aa739 |
| | 3 策略升级 (量化检查清单) | ✅ | c6aa739 |
| **文档** | ml_backtest.md / ml_prompt.md / ml_roadmap.md | ✅ | c6aa739 |

---

## 剩余工作

### P0 — 积累数据（现在开始，被动等待）

| 任务 | 条件 | 预计完成 |
|------|------|---------|
| post-HP3 融合记录积累 ≥200 条 | 正常交易日 5-10 天 | ~6 月 15 日 |
| at 评估所需 5d forward 数据 | 需 ~2 周 trading days | ~6 月 15 日 |
| backtest --force API 恢复 | Eastmoney 等数据源可用 | 不确定 |

### P1 — AT 价值评估（forward 数据就绪后）

检查 TradingAgent (at) 是否有统计显著的预测能力：
- `src/data_loader.py` 已支持读取 TA 日志
- 需要用 fusion_history.csv 中的 tradingagent_score 匹配 forward returns
- 关键未知：at 40.5% strong_bearish 是信号还是噪音？
- at 当前 alpha=0.40，如果评估显示不如随机，应进一步降低

**文件**: `scripts/calibrate_alphas.py`（可扩展为包含 at 评估）

### P2 — Backtest --force 重算（API 恢复后）

用新 52/49 阈值 + HP3 双路径重新评估全部历史记录：
```
对比项目:
  - operation_advice 方向准确率 (5d/10d)  
  - sentiment_score 方向准确率 (5d/10d)
  - 各股票 per-stock 准确率（更新 alpha）
  - fusion_score 方向准确率（首次可算）
```

### P3 — L7_THRESHOLDS 校准（P1+P2 完成后）

c1skill 论证结论：**现在不改 L7 flat zone**。

**原因**:
1. 163 条现有 fusion 记录中 98.2% 的 ML 信号落在 L7 flat zone——这是 pre-HP3 数据
2. at 40.5% strong_bearish 可能是噪音，降低 flat 门槛会放大不确定信号
3. AT 评估未完成 → at 的 bearish 质量未知
4. ±0.5 是合理默认值，无数据表明它有问题

**等待条件**（全部满足后才执行）：

```
条件 A: post-HP3 融合记录 ≥200 条  ← ~6/15
条件 B: AT 评估完成                  ← ~6/15
条件 C: backtest --force 成功运行    ← API 恢复
```

**校准方法**:
1. 遍历 L7_THRESHOLDS 各组合（±0.3, ±0.5, ±0.7, ±1.0 + 非对称变体）
2. 计算每个组合下的方向准确率 + Sharpe 比
3. 选择最优组合更新 `normalizer.py`

**文件**: `src/normalizer.py:42-49` (L7_THRESHOLDS)

### P4 — 月度维护（持续）

```bash
# 每月 1 日 03:00 运行（systemd timer 待部署）
.venv/bin/python scripts/calibrate_alphas.py

# 每次校准后检查报告
cat reports/calibration/$(date +%Y%m%d).md
```

---

## 时间线

```
现在         6/15        7/1         7/15
├────────────┼────────────┼────────────┤
  P0 数据积累        P1 AT评估      P3 L7校准
  (被动等待)         P2 backtest    P4 月校准
```

### Week of 6/7（已完成，已 deploy）

| 日期 | 完成内容 |
|------|---------|
| c6aa739 | 阈值校准、alpha 校准、提示词校准、策略 YAML 升级、context_snapshot 统一、factor_baseline 修复、auto-calibration 脚本 |

### Week of 6/15（数据就绪后执行）

| 优先级 | 任务 | 前置条件 | 工作量 |
|--------|------|---------|--------|
| P1 | AT 价值评估 | forward 数据 ≥5d | 0.5d |
| P2 | backtest --force 重算 | API 恢复 | 2h |
| P3 | L7_THRESHOLDS 校准 | P1+P2 完成 | 2d |
| P4 | systemd timer 部署 calibrate_alphas.py | — | 0.5d |

---

## 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| API 持续不可用，backtest 无法重算 | 🟡 中 | 🟡 中 | 手动补数据，或等下周重试 |
| AT 评估发现 at 为纯噪音 | 🟡 中 | 🟡 中 | 降低 alpha 至 0.20-0.30 |
| post-HP3 ML 仍然大部分 flat | 🟢 低 | 🟡 中 | HP3 计算逻辑已验证有效，但需数据确认 |
| L7_THRESHOLDS 校准不收敛 | 🟢 低 | 🔴 高 | 回退到 ±0.5 默认值，不再改动 |
