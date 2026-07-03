# Aistock_vnpy_Trading — 三系统信号融合平台

## 项目概述

三系统融合决策平台：lynx_vnpy (RF 量化) + MindLynx-Aistock (因子/策略/LLM) + mind_TradingAgent (多智能体辩论)，通过加权投票输出投资建议。

## 目录结构

- `src/` — 核心代码：融合引擎、数据加载、特征桥接、信号融合
- `services/` — 数据服务：东方财富数据、alpha158、ML因子
- `systems/` — 三个子系统各自的目录
- `config/` — settings.yaml, systems.yaml, stock_pool.csv
- `scripts/` — 回测、校准、诊断、部署脚本
- `tests/` — 测试
- `docs/` — 架构决策、子系（子系统说明、数据链文档）

## 常用命令

```bash
# 测试
pytest tests/

# 回测
python scripts/backtest.py

# 校准
python scripts/calibrate_alphas.py

# 每日运行
python scripts/run_daily.py

# 融合引擎诊断
python scripts/diagnose_agreement.py
```

## 模型选择

| 任务 | 推荐模型 |
|------|---------|
| 日常编码、bug修复 | DeepSeek V4 Flash |
| 架构决策、复杂调试 | Ornith 1.0 35B (本地, GPU1) |
| 代码审查、信号分析 | Ornith 1.0 35B (本地) |
| 多模态/可视化 | Qwen 3.6 35B (本地, GPU0) |

## 文档参考

- `docs/opencode-config.md` — OpenCode 配置说明
- `docs/decisions/` — 架构决策记录 (ADR)
- `docs/subsystems/` — 子系统详细说明
- `docs/data-chain/` — 数据链路文档

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Aistock_vnpy_Trading** (13563 symbols, 24970 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/Aistock_vnpy_Trading/context` | Codebase overview, check index freshness |
| `gitnexus://repo/Aistock_vnpy_Trading/clusters` | All functional areas |
| `gitnexus://repo/Aistock_vnpy_Trading/processes` | All execution flows |
| `gitnexus://repo/Aistock_vnpy_Trading/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
