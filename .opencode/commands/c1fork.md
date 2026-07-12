---
description: Run c1fork fork merge audit — scan upstream, classify commits, execute sync
---

Run c1fork — Fork Merge Audit 方法论。

## 使用方式

```
/c1fork scan             运行自动化扫描（Phase 1）
/c1fork protect          生成受保护文件清单（Phase 0.5）
/c1fork review <hash>    查看 BLOCKED 提交详情
/c1fork strategy-d <hash> 用 Strategy D 重实现上游提交
/c1fork backlog          查看未执行的上游提交
/c1fork doc              查看完整方法论文档
```

## 说明

管理 fork 与上游仓库的同步。适用于分叉深度 100+ commits 的高分歧 fork。
核心原则：读上游的思想，不读上游的代码。

参考：`/opt/notes/fork_merge_methodology.md`
