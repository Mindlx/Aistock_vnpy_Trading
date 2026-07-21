# 东方财富数据生态

> 最后更新: 2026-06-24

---

## 目录

| 文档 | 内容 |
|------|------|
| `README.md` | 本索引 + 全景总览 |
| `data-ecosystem.md` | 14 字段详解 + 3 个信号维度 + API 清单 |
| `c1skill-analysis.md` | 是否应作为第四子系统的 8 阶段论证 |
| `research-log.md` | 研究发现记录（持续更新） |

---

## 全景

东方财富平台通过 akshare 提供了丰富的聚合数据，覆盖 **5186 只 A 股**，每个股票 **14 个字段**，以及个股级别的历史时序。这些数据来自东方财富平台的用户行为聚合和统计数据，代表了散户情绪、机构行为和截面相对价值三个独立维度。

### 当前集成状态

| 集成层级 | 状态 | 说明 |
|---------|------|------|
| 自选股缓存 | ✅ 已上线 | 10:53/13:53 fetch 到 `eastmoney_rating.json` |
| 大盘复盘注入 | ✅ 已上线 | "四、资金与情绪"段落注入全市场统计 |
| 个股整点分析 | ✅ 已上线 | pipeline + agent 路径注入个股评级文本 |
| 自选股操盘建议 | ✅ 已上线 | 每只追加 EM 意愿/关注/机构/得分数据 |
| 全市场快照存档 | ✅ 已上线 | 每日 CSV 归档到 `data/research/eastmoney_snapshot/` |
| LLM 路径阈值标签 | ❌ 已移除 | 354条 w/f 校准发现无预测能力, commit 09f58e6 |
| **第四子系统** | ⏳ **待定** | 详见 `c1skill-analysis.md` |

### 数据存档位置

```
data/research/eastmoney_snapshot/
├── snapshot_20260624.csv      ← 全市场5186只×14字段+派生字段
├── snapshot_20260624_summary.json  ← 摘要统计
└── ...（每日追加）
```

### 代码位置

```
services/eastmoney/              ← 主目录（一劳永逸，不受 sync 影响）
├── __init__.py                  包定义
├── fetcher.py                   数据获取+缓存+推送
├── research.py                  全市场快照分析
```

向后兼容入口（仍可用，但新开发请用 services/ 路径）：
```
systems/MindLynx-Aistock/scripts/fetch_eastmoney_rating.py  → 委托到 services/eastmoney/fetcher.py
scripts/research_eastmoney.py                                 → 委托到 services/eastmoney/research.py
```

### 数据存档位置

```
data/research/eastmoney_snapshot/
├── snapshot_20260624.csv          ← 全市场5186只×14字段+派生字段
├── snapshot_20260624_summary.json ← 摘要统计
└── ...（每日追加）
```

---

## 三个信号维度

| 维度 | 核心字段 | 与现有系统的正交性 |
|------|---------|-------------------|
| **A. 散户情绪** | 关注指数, 参与意愿 | LY(技术面) ML(因子+LLM) AT(辩论) — 完全不同 |
| **B. 机构行为** | 机构参与度, 主力成本 | 结构化的机构持仓信息，无系统覆盖 |
| **C. 截面相对价值** | 综合得分, 排名, 上升 | 横截面对比，任何子系统都不做 |

---

## 关键结论

c1skill 完整论证见 `c1skill-analysis.md`。核心结论：

**东方财富数据应在当前路径（数据源→ML prompt）继续积累，不宜在此时作为第四子系统开发。** 至少需要 4-6 周的数据积累和 OOS 验证后才能重新评估。

---

## 参考

- `scripts/fetch_eastmoney_rating.py` — 数据获取入口
- `docs/eastmoney/c1skill-analysis.md` — 第四子系统论证
- `docs/decisions/09f58e6-remove-threshold-labels.md` — 阈值分类移除决策
