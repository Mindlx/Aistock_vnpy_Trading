# OpenCode 配置

> 最后更新: 2026-07-07

## 版本

| 组件 | 版本 |
|:-----|:----:|
| opencode | 1.17.14 |
| @opencode-ai/plugin | 1.17.12 |

## 插件

| 插件 | 用途 |
|:-----|:------|
| `opencode-morph-plugin` | 代码编辑 |
| `opencode-pty` | 终端执行 |
| `opencode-shell-strategy` | Shell 执行策略优化 |
| `opencode-dynamic-context-pruning` | 上下文自动裁剪 |

## Provider

| Provider | 模型 | 接入方式 | 速度 | 适合任务 |
|:---------|:-----|:---------|:----:|:---------|
| `deepseek-flash` | v4-flash | API (api.deepseek.com) | ~100-200 tok/s | 🔵 **主会话调度**、快速响应 |
| `gpu1-ornith` | Ornith 1.0 35B (GPU1) | llama.cpp, port 15433 | **~147 tok/s** | 🟢 代码审查、架构决策、复杂调试 |
| `gpu0-qwen` | Qwen3.6-35B (GPU0) | llama.cpp, port 11434 | **~143 tok/s** | 🟢 文档分析、策略讨论、通用任务 |
| `deepseek-pro` | v4-pro | API (api.deepseek.com) | ~50-100 tok/s | 🔴 高难度推理、长上下文 |

## 系统指令

`~/.config/opencode/instructions.md` — 工作铁律，应用于所有项目。包含数据准确、质疑响应、沟通纪律三项要求。

## 配置文件位置

| 文件 | 用途 |
|:-----|:------|
| `~/.config/opencode/opencode.jsonc` | 主配置：默认模型、插件、Provider、系统指令 |
| `~/.config/opencode/instructions.md` | 系统级工作铁律 |

## Agent 模型分配

| Agent | 模型 | 定位 |
|:------|:-----|:------|
| **build** (默认) | `deepseek-flash` | 主会话，快速响应、工具协调 |
| **plan** | `deepseek-pro` | 规划模式，高难度推理、架构决策 |
| **explore** | `gpu1-ornith` | 探索模式，代码审查、技术调研 |
| **general** (subagent) | `gpu0-qwen` | 通用子任务，文档分析、并行研究 |

## 其他重要配置

| 配置 | 值 | 说明 |
|:-----|:---|:------|
| `default_agent` | `build` | 默认进入 build 模式 |
| `small_model` | `gpu0-qwen` | 标题生成等轻量任务用本地模型 |
| `autoupdate` | `notify` | 有新版本时通知，不自动升级 |
| `shell` | `bash` | 默认 shell |
| `compaction.auto` | `true` | 上下文满时自动压缩 |
| `compaction.prune` | `true` | 裁剪旧工具输出释放上下文 |
| `disabled_providers` | `github-copilot, github-models` | 禁用不用的默认 provider |
