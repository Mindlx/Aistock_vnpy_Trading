# Aistock_vnpy_Trading 文档中心

> 最后更新: 2026-06-24
> 文件总数: 43 篇

---

### 📖 快速导航

| 我想... | 去这里 |
|--------|--------|
| 理解系统整体架构 | `architecture/overview.md` |
| 部署/重启服务 | `operations/deployment.md` |
| 查看当前运行状态 | `architecture/current-state.md` |
| 了解数据从哪里来 | `operations/data-sources.md` |
| 跑回测验证 | `testing/backtest.md` |
| 查看推送格式规范 | `operations/push-format.md` |
| 了解融合权重怎么来的 | `decisions/weight-strategy.md` |
| 查看 ML 提示词 | `subsystems/ml/prompts.md` |

---

### 📂 文档架构

```
docs/
├── README.md                              # ← 本文档索引
│
├── architecture/                          # 架构设计
│   ├── overview.md                        # 系统架构总览（原 architecture.md）
│   ├── current-state.md                   # 运行状态快照
│   ├── data-warehouse.md                  # 数据仓库实现
│   └── data-warehouse-pattern.md          # 数据仓库模式文章
│
├── operations/                            # 运维/配置
│   ├── data-sources.md                    # 数据源配置与依赖审计
│   ├── deployment.md                      # 部署指南
│   └── push-format.md                     # 推送格式规范
│
├── testing/                               # 回测验证
│   └── backtest.md                        # 融合系统回测
│
├── subsystems/                            # 子系统深入
│   └── ml/                                # ML (MindLynx-Aistock)
│       ├── backtest.md                    # ML 回测
│       ├── prompts.md                     # 系统提示词全集
│       └── roadmap.md                     # 改进路线图
│
├── decisions/                             # 活跃架构决策 (ADRs)
│   ├── weight-strategy.md                 # 融合权重策略
│   ├── weight-c1skill-review.md           # 权重 c1skill 审阅
│   ├── semantic-alignment.md              # 语义对齐
│   ├── l7-mapping.md                      # L7 映射
│   ├── mapping-optimization.md            # 映射优化
│   ├── realtime-fusion.md                 # 准实时融合 v2
│   ├── full-system-audit.md               # 全系统审计报告
│   └── at-optimization.md                 # AT 系统优化
│
├── research/                              # 历史研究存档
│   ├── at-bullish-failure-analysis.md     # AT 看多失败分析
│   ├── at-data-injection-implementation.md# AT 数据注入
│   ├── eastmoney-rating-backtest.md       # 东方财富评级回测
│   ├── factor-research-report.md          # 因子研究
│   ├── lynx-vnpy-subsystem-report.md      # LY 子系统报告
│   ├── personal-quant-guide-analysis.md   # 个人量化指南
│   ├── personal-quant-guide-c1skill-review.md
│   ├── realtime-fusion-design.md          # 实时融合 v1（已取代）
│   ├── realtime-fusion-c1skill-review.md  # 融合审阅
│   ├── mapping-c1skill-review.md          # 映射审阅 v1（已取代）
│   ├── archive/                           # 归档（3 篇）
│   └── loop-engineering-research/         # 循环工程研究系列（10 篇）
```

---

### 按读者分组

#### 🛠 运维人员
| 文档 | 位置 |
|------|------|
| 部署指南 | `operations/deployment.md` |
| 数据源配置 | `operations/data-sources.md` |
| 推送格式 | `operations/push-format.md` |
| 当前状态 | `architecture/current-state.md` |

#### 👨‍💻 开发者
| 文档 | 位置 |
|------|------|
| 系统架构 | `architecture/overview.md` |
| 数据仓库 | `architecture/data-warehouse.md` |
| 融合回测 | `testing/backtest.md` |
| ML 提示词 | `subsystems/ml/prompts.md` |
| ML 回测 | `subsystems/ml/backtest.md` |
| ML 路线图 | `subsystems/ml/roadmap.md` |

#### 🔬 研究者
| 文档 | 位置 |
|------|------|
| 融合权重 | `decisions/weight-strategy.md` |
| L7 映射 | `decisions/l7-mapping.md` |
| 语义对齐 | `decisions/semantic-alignment.md` |
| 实时融合 | `decisions/realtime-fusion.md` |
| 全系统审计 | `decisions/full-system-audit.md` |
| AT 优化 | `decisions/at-optimization.md` |
| 历史研究 | `research/` |

---

### 文件命名规范

- 全部使用 **kebab-case**（连字符分隔）
- 文件名即为英文标题，简短描述
- 版本号不体现在文件名中（v1→v2 用取代标记）
- 避免中文文件名（research/ 中的保留原样作为历史存档）

---

### 维护指引

- **新增文档**：放入对应主题目录，在 README.md 中添加索引行
- **文档过时**：在文件开头添加 ⚠️ 标记 + 指向新文档的链接，不删除
- **重命名/移动**：必须使用 `git mv` 而非 cp+rm，以保留历史记录
- **交叉引用**：使用相对路径，如 `../../decisions/weight-strategy.md`
