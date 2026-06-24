# data/research/ — 研究数据目录规范

所有非回测的研究/评估数据存放在此。

## 子目录

| 目录 | 内容 | 更新频率 |
|------|------|---------|
| `eastmoney_snapshot/` | 东方财富全市场日频快照 | 每日 09:53/13:53 |
| `calibration/` | alpha/w/f 阈值校准结果 | 不定期 |

## 规则

1. **`data/backtest/` 不动** — 融合回测数据库（bt_results.db），backtest.py 深度耦合
2. **`config/logs/` 不动** — systemd 日志配置指向此处
3. **新研究数据** → 在 `data/research/` 下新建子目录
4. **每个子目录自带 `README.md`** 说明数据格式和来源
