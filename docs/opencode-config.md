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

| Provider | 模型 | 接入方式 | 优势 | 适合任务 |
|:---------|:-----|:---------|:----|:---------|
| `deepseek-flash` | v4-flash | API (api.deepseek.com) | **低延迟、高吞吐**，云端高速推理 | 🔵 **主会话调度**、日常编码、快速响应 |
| `gpu1-ornith` | Ornith 1.0 35B (GPU1) | llama.cpp, port 15433 | **Agentic coding SOTA**，代码类基准领先 | 🟢 代码审查、架构决策、复杂调试 |
| `gpu0-qwen` | Qwen3.6-35B (GPU0) | llama.cpp, port 11434 | 通义千问系列，通用能力强 | 🟢 文档分析、策略讨论 |
| `deepseek-pro` | v4-pro | API (api.deepseek.com) | 最强推理能力 | 🔴 高难度推理、长上下文分析 |

## 配置文件位置

| 文件 | 用途 |
|:-----|:------|
| `~/.config/opencode/opencode.jsonc` | 主配置：插件列表 |
| 项目根 `.opencode/` | 本地命令和技能定义 |
