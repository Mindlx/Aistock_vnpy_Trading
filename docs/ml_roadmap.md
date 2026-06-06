# ML 子系统 + 融合系统改进路线图

> 基于 2026-06-07 回测数据，c1skill 论证后制定

---

## 三个硬伤

| # | 硬伤 | 现状 | 目标 |
|---|------|------|------|
| HP1 | 权重无数据支撑 | alpha=0.55(估计值) vs 实测 84% | 每月回测自动校准 alpha |
| HP2 | 个股无差异化 | 10 只股票统一权重 | 每只股票独立 alpha，从 DB 动态读取 |
| HP3 | sentiment_score 未直接使用 | 84% 信号通过 24% 的 op_advice 间接参与 | sentiment_score 独立融合路径 |

---

## 时间线

### Week 1-2: 核心修复（当前）

| 日期 | 任务 | 产出 |
|------|------|------|
| 今天 | HP1: auto-calibration 脚本 | `scripts/calibrate_alphas.py` |
| 今天 | HP3: sentiment_score 独立融合路径 | `normalizer.py` + `fusion_engine.py` 改动 |
| 本周 | 部署脚本到 systemd timer | 每月 1 日自动校准 |
| 本周 | 完整跑一次融合回测验证 | 对比校准前后结果 |

### Week 3-4: 观察

| 任务 | 说明 |
|------|------|
| 收集融合日志 | 观察 alpha 调整后的融合决策变化 |
| 监控分歧率 | per-stock alpha 是否减少了无意义分歧 |
| 收集 ML 方向准确率 | 验证 84% directional accuracy 是否持续 |

### Month 2: 优化

| 任务 | 说明 |
|------|------|
| AT 价值评估 | 跑 TradingAgent 独立回测，看 at 信号是否有统计显著的预测能力 |
| 阈值 52/49 切换测试 | AB 对比 60/40 vs 52/49，看融合 Sharpe 差异 |
| flat 降权效果验证 | 确认 L7×0.5 是否改善了融合质量 |

### Month 3: 全面校准

| 任务 | 说明 |
|------|------|
| 动态 alpha 从 DB 读取 | `reliability.py` 从 `backtest_summaries` 表读取准确率，自动计算 alpha |
| context_snapshot schema 统一 | 统一 simple/full 两种格式，使 factor_zscores 和 regime 共存 |
| 全面校准报告 | 输出所有股票的 alpha、准确率趋势、最佳阈值 |

---

## 实施方案

### HP1: Auto-calibration 脚本

**文件**: `scripts/calibrate_alphas.py`

```python
# 伪代码逻辑
def calibrate():
    1. 读取 backtest_summaries (5d window, scope=stock)
    2. 对每只股票: sentiment_direction_accuracy_pct → alpha
       - acc >= 65% → alpha = 0.80
       - acc >= 50% → alpha = 0.65
       - acc >= 25% → alpha = 0.40
       - acc < 25%  → alpha = 0.30
    3. 生成新的 STOCK_ALPHA_OVERRIDE dict
    4. 写入 reliability.py
    5. 记录变更到日志
```

**部署**: systemd timer 每月 1 日 03:00 运行

### HP2: 动态 alpha 从 DB 读取

**当前**: 静态 `STOCK_ALPHA_OVERRIDE` dict in `reliability.py`

**目标**: `reliability.py` 改为查询 `backtest_summaries` 表

```python
@classmethod
def alpha(cls, system, stock_code=None):
    if system == "mindlynx" and stock_code:
        db_alpha = cls._alpha_from_db(stock_code)
        if db_alpha is not None:
            return db_alpha
    return cls.BASE_ALPHA.get(system, 0.50)
```

**风险**: DB 查询增加延迟，需加缓存 (TTL=1h)

### HP3: sentiment_score 独立融合路径

**设计**:

```
当前:
  operation_advice + sentiment_score → normalize_mindlynx() → 1个 L7 信号

改动后:
  ┌─ operation_advice + sentiment_score → normalize_mindlynx() → L7_advice
  └─ sentiment_score alone → normalize_mindlynx_score() → L7_score
  fusion_engine → avg(L7_advice, L7_score) → 最终 ML 信号
```

**normalizer.py 新增**:

```python
@classmethod
def normalize_mindlynx_score(cls, sentiment_score: int) -> float:
    """sentiment_score 直接映射到 L7（不依赖 op_advice）
    score ≥ 60 → +1.5 (谨慎看多), ≤ 40 → -1.5 (谨慎看空)
    flat zone 41-59 → 0.0 (中性，并额外降权 0.5)
    """
    if sentiment_score >= 60:
        return 1.5
    elif sentiment_score <= 40:
        return -1.5
    else:
        return 0.0
```

**fusion_engine.py**: 在 `_fuse_linear()` 和 `_fuse_bayesian()` 中分别计算两个 ML L7 值，取平均作为最终 ML 信号。

---

## 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| auto-calibration 过拟合 | 🟡 中 | 🔴 高 | 只用 >=30 样本的股票；每月最多调整 0.10 |
| sentiment_score 独立路径与现有路径冲突 | 🟡 中 | 🟡 中 | 走 AB 对比：先只记录不决策，观察 2 周 |
| DB 查询影响融合性能 | 🟢 低 | 🟡 中 | 加内存缓存，TTL=1h |
