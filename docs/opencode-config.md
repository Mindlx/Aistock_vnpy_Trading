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
| `gpu1-ornith` | Ornith 1.0 35B (GPU1, port 15433) | OpenAI-compatible, llama.cpp | **Agentic coding SOTA**，SWE-Bench 75.6，Terminal-Bench 64.2 | 代码审查、架构决策、复杂调试、代码生成 |
| `gpu0-qwen` | Qwen3.6-35B (GPU0, port 11434) | OpenAI-compatible, llama.cpp | 通义千问系列，通用能力强，多语言支持好 | 文档分析、策略讨论、日常编码 |
| `deepseek` | v4-flash / v4-pro | API (api.deepseek.com) | 云端高速推理，无本地资源限制 | 高难度推理、长上下文分析 |

## 配置文件位置

| 文件 | 用途 |
|:-----|:------|
| `~/.config/opencode/opencode.jsonc` | 主配置：插件列表 |
| 项目根 `.opencode/` | 本地命令和技能定义 |
