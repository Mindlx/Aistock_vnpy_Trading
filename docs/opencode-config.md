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

| Provider | 模型 | 接入方式 |
|:---------|:-----|:---------|
| DeepSeek | v4-flash / v4-pro | API (api.deepseek.com) |

## 配置文件位置

| 文件 | 用途 |
|:-----|:------|
| `~/.config/opencode/opencode.jsonc` | 主配置：插件列表 |
| 项目根 `.opencode/` | 本地命令和技能定义 |
