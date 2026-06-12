# 东方财富评级 — w/f 阈值回测计划

## 目的

当前 `_combined_grade()` 函数中的 w（参与意愿）阈值（80/65/55/45/20）和 f（关注指数）阈值（80/65/55）是经验设定值，未经数据验证。本文件记录后续回测计划，用数据驱动方式确定最优阈值分档。

## 数据采集

每次 `generate_rating_report.py` 运行时，在 `data/realtime/eastmoney_wf_log.csv` 追加一行：

```
date,stock_code,stock_name,willingness,focus,icon,conclusion,l7_level
20260608,600372,中航机载,41.17,73.8,📉,偏空信号 高位关注 抛压加剧,-2/-1
```

字段说明：
- `willingness` = 当日参与意愿值 (0-100)
- `focus` = 当日关注指数均值 (0-100)
- `icon` = L7 分级图标
- `conclusion` = 结论描述（占位用）
- `l7_level` = L7 分级标签（+3/+2/+1/0/-2/-1/-3）

## 回测方法

1. 对每条记录，从 `stock_analysis.db.stock_daily` 获取该股票的**次日涨跌幅** `pct_chg`
2. 按 w/f 值分桶，统计每个分桶的上涨概率（次日涨跌幅>0 的比例）
3. 与当前阈值对比，确认是否调整

## 最小样本量

按二项分布公式：`n = Z²·p·(1-p) / d²`

取 Z=1.96 (95% 置信度)、p=0.5 (假设无预测能力)、d=0.05 (±5%)：
→ **需要 384 个样本才能以 ±5% 精度校准阈值**

当前每日 10 只股票，**约 38 个交易日（2 个月）后可做首次分析**。

## 参考

- `scripts/generate_rating_report.py` — `_combined_grade()` 函数
- `data/realtime/eastmoney_wf_log.csv` — 采集数据
- `data/realtime/prob_up_log.csv` — ly prob_up 回测数据（类似方法）
- `scripts/backfill_prob_up.py` — prob_up 回填脚本（设计模式参考）
