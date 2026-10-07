# Aistock_vnpy_Trading 文档中心

> 最后更新: 2026-10-07

## 📖 推荐阅读

| 优先级 | 文档 | 说明 |
|:------:|------|------|
| 🥇 | `architecture/system-overview.md` | 系统架构全景 |
| 🥇 | `architecture/current-state.md` | 当前状态快照 |
| 🥇 | `architecture/deployment.md` | 部署与 systemd 定时/交易日门禁 |
| 🥇 | `data-chain/data-warehouse.md` | 数据仓库16类数据 |
| 🥇 | `subsystems/at.md` | AT 子系统 |
| 🥇 | `subsystems/ly/architecture.md` | LY 子系统（含 LGB 模型溯源） |
| 🥇 | `subsystems/ml/backtest.md` | ML 子系统回测（含 regime beta 警示） |
| 🥇 | `push/format.md` | 推送格式规范 |

## 📖 按子系统

| 系统 | 文档 |
|------|------|
| **融合引擎** | `data-chain/data-warehouse.md`, `push/format.md`, `architecture/current-state.md` |
| **ML (MindLynx)** | `subsystems/ml/backtest.md`, `data-chain/data-sources.md` |
| **AT (TradingAgent)** | `subsystems/at.md`, `llm/injection.md` |
| **LY (lynx_vnpy)** | `subsystems/ly/architecture.md` |
| **研究** | `research/README.md`（研究索引）, `research/c1skill-ml-regime-alpha-2026-10-03.md` |

## 🗂 留痕索引（专项）

| 文档 | 主题 |
|------|------|
| `research/README.md` | 研究文档索引（含 ML regime beta 论证） |
| `incidents/README.md` | 事故与故障留痕索引 |
| `subsystem-fork-management.md` | 子系统上游管理 + ML 读取点防护记录 |

## 🕒 近期专项留痕

| 日期 | 文档 | 主题 |
|------|------|------|
| 2026-10-07 | `subsystems/ly/architecture.md` | LGB 模型溯源 sidecar + `--verify` 漂移校验 |
| 2026-10-03 | `research/c1skill-ml-regime-alpha-2026-10-03.md` | ML 准确率=regime beta（非退化），权重 HOLD |
| 2026-10-03 | `incidents/2026-10-03-fusion-row-contamination.md` | `analysis_history` 的 'fusion' 行污染与防护 |
| 2026-10-02 | `incidents/2026-10-02-data-units-alignment.md` | 数据单位对齐（volume=股/amount=元） |

## 📂 目录结构

```
docs/
├── architecture/        系统架构与部署（overview/deployment/current-state/resource-profile）
├── audit/               审计报告
├── changelog/           变更日志(已归档，见 changelog/README.md)
├── data-chain/          数据链与仓库
├── decisions/           架构决策(已归档，见 decisions/README.md)
├── deployment/          部署与恢复指南
├── design/              设计与实施计划
├── eastmoney/           东方财富分析
├── goose-doc/           Goose 遗留文档
├── incidents/           事故与故障留痕（见 incidents/README.md）
├── llm/                 LLM 注入与路线图
├── plan/                计划
├── push/                推送格式
├── research/            研究重点方向（见 research/README.md）
├── subsystems/          子系统文档（ly/ ml/ at.md）
├── superpowers/         规划草稿
├── opencode-config.md   OpenCode 配置
├── protected_files.txt  受保护文件清单
├── subsystem-fork-management.md  子系统上游管理
├── tools-inventory.md   工具清单
└── README.md            本文档
```
