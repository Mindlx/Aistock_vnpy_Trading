---
name: c1fork
description: Fork Merge Audit — 管理 fork 与上游仓库的同步。扫描→评估→执行→审计完整闭环，适用于高分歧 fork（100+ commits）。
---

# Fork Merge Audit

## 用途

管理 fork 项目与上游仓库之间的同步。提供从扫描 → 评估 → 执行 → 审计的完整闭环。

## 前置条件

- 有指向上游的 git remote
- Python 3.8+
- `~/.agents/skills/fork-merge-audit/fork_merge_audit.py` 可用

## 快速开始

```bash
python ~/.agents/skills/fork-merge-audit/fork_merge_audit.py --output report.json
```

输出：SAFE=N  NEEDS_REVIEW=N  BLOCKED=N

## 完整流程

```
Phase 0: 基础设施准备（remote + 脚本）
Phase 0.5: Pre-Merge 受保护文件清单（@calibration 标记）
Phase 1: 自动化扫描（fork_merge_audit.py）
Phase 2: 模块化聚合（将 commits 按功能模块分组）
Phase 3: FE² 评估（5 轴对比：CORRECT/MAINTAIN/PERFORM/INTEGRATE/ROBUST）
Phase 4: 执行方案编排（P0→P3 排序）
Phase 5: 三路执行（cherry-pick / Strategy D / Backlog）
Phase 6: 验证门禁（编译 + 测试 + 烟雾）
Phase 7: 审计存档
```

## 三路执行策略

| 策略 | 适用 | 操作 |
|------|------|------|
| Strategy A: 直接 cherry-pick | SAFE（0 冲突文件） | `git cherry-pick <hash>` |
| Strategy B: 条件 cherry-pick | NEEDS_REVIEW（1-2 冲突） | cherry-pick + 修冲突 |
| **Strategy D: 选择性重实现** | **BLOCKED（3+ 冲突）** | **读思想不读代码，本架构重实现** |

## Strategy D 详细步骤

```bash
Step 1: 理解意图
  git show <hash>
  git log -1 --format=%b <hash>

Step 2: 解耦核心逻辑
  提取不依赖上游架构的函数/类（通常仅 20-40% 是核心）

Step 3: 在本架构上重新实现
  保持本地的设计模式，不强行适配上游架构

Step 4: 合入验证
  py_compile + 烟雾测试
```

## 受保护文件 @calibration

在关键优化代码旁添加 `@calibration` 注释标记，合入上游时自动检测。

## 参考

- 完整方法论文档：`/opt/notes/fork_merge_methodology.md`
- 扫描脚本：`~/.agents/skills/fork-merge-audit/fork_merge_audit.py`
- 上游 backlog：`docs/upstream_backlog.md`
