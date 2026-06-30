# Aistock_vnpy_Trading 文档中心

> 最后更新: 2026-06-24
> 文件总数: 45 篇

---

## 📖 视图 A：按关注域（推荐）

| 关注域 | 核心文档 | 涉及子系统 |
|--------|---------|-----------|
| 🔗 **数据链** | `data-chain/overview.md`, `data-chain/data-sources.md`, `data-chain/data-warehouse.md`, `data-chain/data-warehouse-pattern.md` | LY+ML+AT+融合 |
| 📲 **信息推送** | `push/format.md`, `push/architecture-review.md`, `push/wechat-architecture.md` | 融合+ML |
| 🧠 **LLM/提示词** | `llm/prompts.md`, `llm/injection.md`, `llm/roadmap.md` | ML+AT |
| 🏛 **系统架构** | `architecture/overview.md`, `architecture/current-state.md`, `architecture/deployment.md`, `architecture/system-resource-profile.md` | 全部 |
| 🧪 **回测验证** | `testing/backtest.md`, `subsystems/ml/backtest.md` | 融合+ML |
| 📐 **架构决策** | `decisions/` (8 篇) | 全部 |
| 💹 **东方财富数据** | `eastmoney/README.md`, `eastmoney/c1skill-analysis.md`, `eastmoney/data-ecosystem.md` | 🆕 EM |
| 🗄 **历史研究** | `research/` (23 篇) | 全部 |

## 📖 视图 B：按子系统

| 系统 | 相关文档 |
|------|---------|
| **融合引擎** | `data-chain/data-warehouse.md`, `testing/backtest.md`, `decisions/weight-strategy.md`, `decisions/realtime-fusion.md`, `decisions/semantic-alignment.md`, `push/format.md` |
| **ML (MindLynx)** | `subsystems/ml/backtest.md`, `llm/prompts.md`, `llm/roadmap.md`, `data-chain/data-sources.md` |
| **AT (TradingAgent)** | `llm/injection.md`, `decisions/at-optimization.md`, `data-chain/data-sources.md` |
| **LY (lynx_vnpy)** | `data-chain/data-sources.md`, `research/lynx-vnpy-subsystem-report.md` |
| **EM (东方财富)** | `eastmoney/README.md`, `eastmoney/c1skill-analysis.md`, `eastmoney/data-ecosystem.md` |

---

## 📂 物理目录

```
docs/
├── README.md                  ← 本文档索引
├── architecture/              # 系统架构（4篇）
│   ├── overview.md            # 系统架构总览
│   ├── system-overview.md     # 🆕 架构全景（分层/子系统/融合/推送/c1test）
│   ├── current-state.md       # 运行状态快照
│   └── deployment.md          # 部署指南
│
├── data-chain/                # 🆕 数据链（4篇）
│   ├── overview.md            # 🆕 数据链总览
│   ├── data-sources.md        # 数据源配置与依赖审计
│   ├── data-warehouse.md      # 数据仓库实现
│   └── data-warehouse-pattern.md  # 数据仓库模式文章
│
├── push/                      # 信息推送（3篇）
│   ├── format.md              # 推送格式规范
│   ├── architecture-review.md # 推送架构评估
│   └── wechat-architecture.md # 微信推送架构
│
├── llm/                       # 🆕 LLM/提示词（4篇）
│   ├── prompts.md             # ML 系统提示词全集
│   ├── injection.md           # 🆕 跨系统信号注入
│   └── roadmap.md             # LLM 改进路线图
│
├── subsystems/                # 子系统独立文档
│   └── ml/backtest.md         # ML 子系统回测
│
├── testing/                   # 回测验证（3篇）
│   ├── backtest.md             # 融合系统回测 + 🆕 c1test 统一回测
│   └── backtest-inventory.md   # 🆕 回测资产全清单
│
├── eastmoney/                  # 🆕 东方财富数据（4篇）
│   ├── README.md              # 全景总览
│   ├── c1skill-analysis.md    # 第四子系统论证
│   ├── data-ecosystem.md      # 14字段+3信号维度
│   └── research-log.md        # 研究发现日志
│
├── reflections/               # 系统里程碑对话录（1篇）
│   └── 2026-06-29-system-milestone.md # 🆕 c1test 上线日 — 基线诊断与愿景
│
├── decisions/                 # 活跃架构决策（10篇）
│   ├── l7-mapping.md            # L7 映射对齐 (Oracle+c1skill)
│   ├── semantic-alignment.md    # 三系统语义对齐原设计
│   ├── accuracy-calibrated-mapping.md # 🆕 ml精度校准映射 v4.0
│   ├── ly-ml-agreement-boost.md # ly+ml同向增益
│   ├── at-optimization.md       # AT 优化
│   ├── full-system-audit.md     # 全系统审计
│   ├── weight-c1skill-review.md # 权重 c1skill 审阅
│   ├── weight-strategy.md       # 权重策略
│   ├── realtime-fusion.md       # 准实时融合
│   └── mapping-optimization.md  # 映射优化分析
│
└── research/                  # 历史研究存档（23篇）
    ├── archive/
    ├── loop-engineering-research/
    └── (单篇分析报告)
```

---

## 文件命名规范

- **kebab-case**（连字符分隔），中文文件名仅限 research/ 历史存档
- 所有移动/重命名使用 `git mv` 保留历史

## 维护指引

- **新增文档**：按关注域放入对应目录，更新 README.md
- **文档过时**：文件开头添加 ⚠️ 标记 + 指向新文档的链接，不删除
