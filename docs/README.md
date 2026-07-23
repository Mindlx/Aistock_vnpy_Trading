# Aistock_vnpy_Trading 文档中心

> 最后更新: 2026-07-22

## 📖 推荐阅读

| 优先级 | 文档 | 说明 |
|:------:|------|------|
| 🥇 | `architecture/system-overview.md` | 系统架构全景 |
| 🥇 | `architecture/current-state.md` | 当前状态快照 |
| 🥇 | `data-chain/data-warehouse.md` | 数据仓库16类数据 |
| 🥇 | `subsystems/at.md` | AT 子系统 |
| 🥇 | `subsystems/ly/architecture.md` | LY 子系统 |
| 🥇 | `push/format.md` | 推送格式规范 |

## 📖 按子系统

| 系统 | 文档 |
|------|------|
| **融合引擎** | `data-chain/data-warehouse.md`, `push/format.md`, `architecture/current-state.md` |
| **ML (MindLynx)** | `subsystems/ml/backtest.md`, `data-chain/data-sources.md` |
| **AT (TradingAgent)** | `subsystems/at.md`, `llm/injection.md` |
| **LY (lynx_vnpy)** | `subsystems/ly/architecture.md` |

## 📂 目录结构

```
docs/
├── architecture/        系统架构与部署
├── changelog/           变更日志(已归档)
├── data-chain/          数据链与仓库
├── decisions/           架构决策(已归档)
├── eastmoney/           东方财富分析
├── goose-doc/           Goose 遗留文档
├── llm/                 LLM 注入与路线图
├── push/                推送格式
├── subsystems/          子系统文档
├── opencode-config.md   OpenCode 配置
├── protected_files.txt  受保护文件清单
├── subsystem-fork-management.md  子系统上游管理
├── tools-inventory.md   工具清单
└── README.md            本文档
```
