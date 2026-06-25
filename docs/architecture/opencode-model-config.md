# OpenCode 模型配置

> 最后更新: 2026-06-25

---

## 三层模型架构

| 层级 | 模型 | 覆盖 | 费用 |
|:----:|:----|:----:|:----:|
| **L1 日常** | Nemotron-30B (本地 GPU1, 15433端口) | 85% 流量 | **免费** |
| **L2 快速** | DeepSeek v4-flash (云端 API) | 5% 流量 | $0.14/M |
| **L3 深度** | DeepSeek v4-pro (云端 API) | 10% 流量 | $0.435/M |

## 代理到模型分配

### agents（子代理）

| 代理 | 模型 | 用途 |
|:-----|:-----|:------|
| sisyphus | Nemotron | 主代理 (编排、代码、分析) |
| sisyphus-junior | Nemotron | 任务执行 |
| hephaestus | Nemotron | 构建任务 |
| oracle | DeepSeek v4-pro | 复杂架构、疑难调试 |
| librarian | Nemotron | 代码搜索 |
| explore | Nemotron | 代码探索 |
| multimodal-looker | DeepSeek v4-flash | 图片/PDF 分析 |
| metis | Nemotron | 规划咨询 |
| momus | Nemotron | 计划审查 |
| prometheus | Nemotron | 规划 |
| atlas | Nemotron | 探索 |

### categories（按类别）

| 类别 | 模型 | 用途 |
|:-----|:-----|:------|
| visual-engineering | Nemotron | UI/前端 |
| ultrabrain | DeepSeek v4-pro | 硬逻辑/算法 |
| deep | DeepSeek v4-pro | 深入研究 |
| artistry | Nemotron | 创意任务 |
| quick | Nemotron | 简单修改 |
| unspecified-low | Nemotron | 低难度 |
| unspecified-high | Nemotron | 中难度 |
| writing | Nemotron | 文档写作 |

## 相关文件

- `~/.bashrc` — ~~`export OPENCODE_MODEL=deepseek/deepseek-v4-pro`~~ (已删除，避免覆盖 per-agent 配置)
- `~/.config/opencode/oh-my-openagent.json` — 代理/类别到模型的映射表
- `opencode.json` — provider 定义 (gpu0-local/qwen, gpu1-local/cascade2-30b, deepseek/flash+pro)

## 本地模型部署

| 模型 | 端口 | GPU | 内存 | 速度 |
|:-----|:----:|:---:|:----:|:----:|
| Qwen3.6-27B | 11434 | GPU 0 | ~18GB | 37 tok/s (有 reasoning bug) |
| **Nemotron-Cascade-2-30B** | **15433** | **GPU 1** | **~20GB** | **212-247 tok/s** |

两者均为 Docker 容器运行 (`ghcr.io/ggml-org/llama.cpp:server-cuda`)。

## 变更历史

| 日期 | 变更 |
|:-----|:------|
| 2026-06-25 | 删除 `OPENCODE_MODEL` 环境变量（避免覆盖 per-agent 配置） |
| 2026-06-25 | `sisyphus/librarian/explore/writing/unspecified-high` 从 flash→Nemotron |
| 2026-06-25 | `metis/momus/prometheus/atlas` 从 flash→Nemotron |
| 2026-06-25 | `oh-my-openagent.json` 6处 Qwen→Nemotron 引用更新 |
