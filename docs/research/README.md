# 研究文档

> 本目录存放近期研究重点方向的调研分析与论证记录。

## 目录

| 文件 | 主题 | 日期 |
|------|------|------|
| `turnover-chip-cross-analysis.md` | ~~换手率 × 筹码分布交叉效应分析~~ **已关闭** | 2026-07-23 |
| `prompt-optimization.md` | LLM Prompt 注入优化（工具描述精简/去重/合并策略/清理死代码） | 2026-07-23 |
| `skill-accuracy-tracking.md` | 策略级准确率追踪（`research_skill_accuracy.py`） | 2026-07-23 |
| `chip-concentration-factor.md` | 筹码集中度横截面因子（IC=-0.111, 已因子化 #14） | 2026-07-23 |
| `ml-scoring-asymmetry.md` | ML评分方向不对称 — 14因子看多85% vs LLM看空69% | 2026-07-27 |

---

## 数据资产

本研究产生的数据存放在 `data/data_warehouse.db`，可通过以下命令再生：

| 数据 | 位置 | 行数 | 再生命令 |
|------|------|:----:|---------|
| 日K线 (216只) | `daily_ohlcv` 表 | 54667 | 扩展数据: `python scripts/expand_stock_pool.py --skip-chip --skip-turnover` |
| 换手率 (216只) | `daily_ohlcv.turnover` 列 | 52185 | 回填: `python scripts/expand_stock_pool.py --skip-ohlcv --skip-chip` |
| 筹码分布 (216只) | `chip_distribution` 表 | 28464 | 回填: `python scripts/compute_chip_local.py --backfill` |
| 行业 (216只) | `fundamentals.industry` 列 | 216 | 自动从 Tushare 拉取 |

> **自动维护**: scheduler 每日 15:45 自动对所有 216 只股票执行换手率回填(Tushare) + 筹码分布(本地计算)，研究数据持续积累。无需手动干预。
