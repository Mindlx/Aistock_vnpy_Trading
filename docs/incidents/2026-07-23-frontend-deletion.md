# 事故报告：前端 `apps/dsa-web/` 被误删除

> **日期**: 2026-07-23  
> **严重程度**: HIGH — WebUI 前端代码从工作目录中丢失，构建断裂  
> **根因**: `rsync --delete` 在不了解双向文件差异的情况下执行  
> **恢复**: 从上游 git HEAD~1 恢复

---

## 时间线

| 时间 | 事件 |
|:----:|:------|
| 全天 | 在 `systems/MindLynx-Aistock/` 中开发（因子/策略/文档等）|
| ~20:45 | 执行 `rsync -a --delete systems/MindLynx-Aistock/ /home/bluekuma/workspace/MindLynx-Aistock/` |
| | → `apps/` 被 `--delete` 删除（systems/ 中没有此目录）|
| ~20:50 | 执行 `bash scripts/sync_systems.sh`（反向同步）|
| | → `apps/` 已删除的状态被同步回 `systems/` |
| ~21:20 | 用户发现 WebUI 无法访问 |
| ~21:25 | 追查发现 `apps/` 从两个工作目录中失踪 |
| ~21:30 | 从上游 git HEAD~1 恢复 |
| ~21:35 | 构建发现 TypeScript 错误（上游品牌重命名残留）|

## 根因分析

### 关键线索

`config/systemd/Aistock_vnpy_Trading-webui.service` 暴露根因：

```ini
Environment=PYTHONPATH=/home/bluekuma/workspace/MindLynx-Aistock
```

WebUI 服务**直接从上游 fork 目录运行**，不是从 `systems/` 运行。所以 `rsync --delete` 直接删除了正在运行的服务的依赖文件。

### 直接原因

```bash
# 破坏性操作:
rsync -a --delete systems/MindLynx-Aistock/ /home/bluekuma/workspace/MindLynx-Aistock/
# ^ 这一行从上游 fork 中删除了 apps/ (因为 systems/ 中没有)
# ^ 上游 fork 是 WebUI 服务的 PYTHONPATH
# ^ 删除后 WebUI 立即崩溃

# 二次破坏:
bash scripts/sync_systems.sh
# ^ 把"无 apps"状态同步回 systems/
```

### 根本原因

1. **没有意识到 WebUI 从上游 fork 运行**: `PYTHONPATH` 的指向与 rsync 的来源不一致
2. **`rsync --delete` 在执行前未评估后果**: 从 `systems/`（无 `apps/`）同步到上游（有 `apps/`）时，`--delete` 会删掉上游独有的全部文件
3. **备份机制有盲区**: `sync_systems.sh` 的备份只覆盖 `LOCAL_CHECKS`，`apps/` 不在其中
4. **WebUI 未被识别为关键资产**: 在重构/清理过程中 `apps/` 被视为可丢弃的冗余代码（commit 4c26901），但服务配置仍依赖它

## 影响

| 项目 | 状态 |
|:-----|:------|
| `apps/dsa-web/` 源码 | ✅ 已从 git HEAD~1 恢复 |
| `node_modules/` | ✅ 存在（之前遗留的）|
| `npm install` | ✅ 通过 |
| `npm run build` | ❌ TypeScript 错误（上游品牌重命名 `DSA→MLA` 残留）|
| WebUI 服务 | ❌ 构建失败，无法启动 |

前端构建断裂是上游代码本身的质量问题（TS 类型不匹配），不是本次恢复引入的。

## 改进措施

| # | 措施 | 类型 |
|:-:|:-----|:------|
| 1 | rsync 操作前执行 `--dry-run` 预览要删除的文件 | **流程** |
| 2 | 识别并保护关键目录清单（`apps/`、`strategies/` 等）| **工具** |
| 3 | `sync_systems.sh` 的备份机制扩展为全目录快照 | **工具** |
| 4 | 在文档中记录 `systems/` 与上游 fork 之间的已知差异 | **文档** |

---

## 恢复命令（备忘）

```bash
# 从上游 git 历史恢复 apps/
cd /home/bluekuma/workspace/MindLynx-Aistock
git checkout HEAD~1 -- apps/dsa-web/

# 同步到本系统
rsync -a /home/bluekuma/workspace/MindLynx-Aistock/apps/dsa-web/ \
  /home/bluekuma/workspace/Aistock_vnpy_Trading/systems/MindLynx-Aistock/apps/dsa-web/
```
