# OpenCode 配置参考

> 最后更新: 2026-06-26

---

## 配置文件位置

| 文件 | 用途 |
|:-----|:------|
| `~/.config/opencode/opencode.json` | 主配置：provider、MCP、插件 |
| `~/.config/opencode/oh-my-openagent.json` | 代理级模型分配（覆盖 opencode.json 的 model 字段） |
| `~/.config/opencode/acp.jsonc` | ACP (Auto Context Processing) 插件配置 |
| `~/.config/opencode/supermemory.jsonc` | Supermemory 持久记忆配置 |

## Provider

| Provider | 模型 | 位置 | 用途 |
|:---------|:-----|:-----|:-----|
| gpu0-local | Qwen3.6-27B | `127.0.0.1:11434` | 日常轻量任务 |
| gpu1-local | Qwen3.6-35B | `127.0.0.1:15433` | 主力本地模型 |
| deepseek | v4-flash / v4-pro | `api.deepseek.com` | 云端推理 |

## 模型分配策略（oh-my-openagent.json）

| 模型 | 分配代理 / 分类 |
|:-----|:----------------|
| **Qwen3.6-35B（本地）** | hephaestus, librarian, explore, sisyphus-junior, visual-engineering, artistry, quick, unspecified-low, writing |
| **DeepSeek v4-flash** | sisyphus, multimodal-looker, prometheus, metis, momus, atlas, unspecified-high |
| **DeepSeek v4-pro** | oracle, ultrabrain, deep |

约 **85%** 日常流量走本地 Qwen，**10%** 最难任务走 Pro，**5%** 中端走 Flash。

## 已安装插件

| 插件 | 用途 | 状态 |
|:-----|:-----|:----:|
| `@morphllm/opencode-morph-plugin` | Morph 编辑工具 | ✅ 启用 |
| `opencode-pty` | PTY 终端支持 | ✅ 启用 |
| `oh-my-openagent@latest` | 代理模型分配 | ✅ 启用 |
| `opencode-token-monitor` | Token 用量监控 | ✅ 启用 |
| `opencode-supermemory` | 持久记忆 | ✅ 启用 |
| `opencode-acp` | 自动上下文处理 | ✅ 启用 |
| `opencode-dynamic-context-pruning` | 动态上下文裁剪 | ✅ 启用 (2026-07-07) |
| `opencode-shell-strategy` | Shell 执行策略优化 | ✅ 启用 (2026-07-07) |

## MCP 服务

| 服务 | 状态 | 用途 |
|:-----|:----:|:------|
| `codebase-memory-mcp` | ✅ 启用 | 代码知识图谱 |
| `gitnexus` | ❌ 禁用 | 原代码分析（已由 codebase-memory 替代） |

## 可清理的遗留文件

| 文件 | 说明 |
|:-----|:------|
| `~/.config/opencode/*.backup-*` (×8) | 旧备份，当前配置已稳定 |
| `~/.config/opencode/config.json` | 0 字节空文件 |
| `~/.config/opencode/opencode.jsonokbak` | 旧备份 |
| `~/.config/opencode/dcp.jsonc.backup` | DCP 备份 |
| `~/.opencode/messages/` | 历史消息缓存（可选清理） |
| `~/.opencode/token-history/` | Token 历史（可选清理） |
