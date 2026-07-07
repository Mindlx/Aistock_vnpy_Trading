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

## 配置文件位置

| 文件 | 用途 |
|:-----|:------|
| `~/.config/opencode/opencode.jsonc` | 主配置：插件列表 |
| 项目根 `.opencode/` | 本地命令和技能定义 |
