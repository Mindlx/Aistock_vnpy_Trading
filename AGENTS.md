# Aistock_vnpy_Trading — 三系统信号融合平台

## 项目概述

三系统融合决策平台：lynx_vnpy (RF 量化) + MindLynx-Aistock (因子/策略/LLM) + mind_TradingAgent (多智能体辩论)，通过加权投票输出投资建议。

## 目录结构

- `src/` — 核心代码：融合引擎、数据加载、特征桥接、信号融合
- `services/` — 数据服务：东方财富数据、alpha158、ML因子
- `systems/` — 三个子系统各自的目录
- `config/` — settings.yaml, systems.yaml, stock_pool.csv
- `scripts/` — 回测、校准、诊断、部署脚本
- `tests/` — 测试
- `docs/` — 架构决策、子系统说明、数据链文档

## 常用命令

```bash
# 测试
pytest tests/

# 回测
python scripts/backtest.py

# 校准
python scripts/calibrate_alphas.py

# 每日运行
python scripts/run_daily.py

# 融合引擎诊断
python scripts/diagnose_agreement.py

# 部署 systemd 配置（修改 config/systemd/ 后必须执行）
bash scripts/deploy-systemd.sh

# 如需重启常驻 daemon（非交易时段，需用户确认）
bash scripts/deploy-systemd.sh --restart-daemons

# 验证 systemd 状态
systemctl --user list-timers --no-pager | grep -E 'Aistock|aistock|c1test'
systemctl --user list-units --all | grep 'Aistock_vnpy_Trading' | grep running
```

## 模型选择

| 任务 | 推荐模型 | Provider |
|------|---------|---------|
| 日常编码、bug修复 | DeepSeek V4 Flash | 系统默认 |
| 架构决策、复杂调试 | Qwen3.6-27B-Dense (llama.cpp, GPU0) | `lla-qwen27B` |
| 代码审查、代码补全 | Qwen3.6-27B-Dense (llama.cpp, GPU0) | `lla-qwen27B` |
| 文档分析、并行研究 | Qwen3.6-35B-MoE (SGLang, GPU1) | `lla-selode` |

## 文档参考

- `docs/opencode-config.md` — OpenCode 配置说明
- `docs/decisions/` — 架构决策记录 (ADR)
- `docs/subsystems/` — 子系统详细说明
- `docs/data-chain/` — 数据链路文档
