# 部署指南

## 环境要求

- Python >= 3.13
- OS: Linux (建议 Ubuntu 22.04+)
- systemd (用户级 service 管理)

## 一键安装

融合系统与三个子系统的所有依赖统一管理：

```bash
# 1. 进入项目目录
cd /home/bluekuma/workspace/Aistock_vnpy_Trading

# 2. 融合系统环境（含 ly + TA 依赖）
.venv/bin/pip install -r requirements.txt

# 3. MindLynx 子系统的独立环境
systems/MindLynx-Aistock/.venv/bin/pip install -r systems/MindLynx-Aistock/requirements.txt
```

> 注：ly (lynx_vnpy) 通过 fusion `.venv` 直接调用，无需单独环境。
> TA (TradingAgent) 依赖已内置在 fusion `.venv` 中。

## 配置

```bash
# 企业微信 Webhook（在 settings.yaml 中已预置）
# 如需更换，编辑:
vim config/settings.yaml
#   wecom.webhook_url: 替换为你的 Webhook 地址
#   wecom.enabled: true

# TradingAgent DeepSeek API Key
cat systems/mind_TradingAgent/.env
# DEEPSEEK_API_KEY=sk-xxx  已配置

# MindLynx DeepSeek API Key
cat systems/MindLynx-Aistock/.env
# LLM_DEEPSEEK_API_KEY=sk-xxx  已配置
```

## systemd 服务管理

### 服务总览

| Service | 类型 | 运行内容 | 状态 |
|---------|------|---------|------|
| `Aistock_vnpy_Trading-monitor.service` | `simple` | MindLynx 盘中实时监控 | `active (running)` |
| `Aistock_vnpy_Trading-scheduler.service` | `simple` | MindLynx 定时分析调度 | `active (running)` |
| `Aistock_vnpy_Trading-fusion.service` | `oneshot` | 融合引擎日终分析 (15:30) | `inactive (timer)` |
| `Aistock_vnpy_Trading-TA.service` | `oneshot` | TradingAgent 深度论证 (16:00) | `inactive (timer)` |

### 启用全部服务

```bash
# MindLynx daemon（需手动启动一次，之后自动重启）
systemctl --user start Aistock_vnpy_Trading-monitor.service
systemctl --user start Aistock_vnpy_Trading-scheduler.service

# 定时器（已启用，周一至周五自动触发）
systemctl --user enable --now Aistock_vnpy_Trading-fusion.timer
systemctl --user enable --now Aistock_vnpy_Trading-TA.timer
```

### 查看状态

```bash
# 所有服务状态
systemctl --user list-units --all | grep aistock

# 定时器下次触发时间
systemctl --user list-timers | grep aistock

# 查看日志
journalctl --user -u Aistock_vnpy_Trading-fusion.service --since today
journalctl --user -u Aistock_vnpy_Trading-monitor.service -f
```

### 服务文件路径

所有 service/timer 文件位于 `~/.config/systemd/user/`，以 `Aistock_vnpy_Trading-` 为前缀。

## 运行方式

```bash
# 手动触发当日融合
.venv/bin/python scripts/run_daily.py

# 指定日期融合
.venv/bin/python scripts/run_daily.py --date 2026-05-29

# 指定融合模式（linear / bayesian / dual）
.venv/bin/python scripts/run_daily.py --mode dual

# 仅打印不推送
.venv/bin/python scripts/run_daily.py --dry-run

# 触发 TradingAgent 分析
.venv/bin/python scripts/run_daily.py --run-ta

# 模拟测试
.venv/bin/python scripts/run_daily.py --mock
```

## 融合模式

| 模式 | 说明 | 配置 |
|------|------|------|
| `linear` | 线性加权 + 分歧检测（默认） | `config/settings.yaml → fusion_mode: linear` |
| `bayesian` | 可靠性调制贝叶斯融合 | `fusion_mode: bayesian` |
| `dual` | 同时输出两种结果供对比 | `fusion_mode: dual` 或 `--mode dual` |

## 交易日自动运行流程

```
09:30 开盘
  ├─ MindLynx monitor daemon → 盘中实时盯盘推送
15:00 收盘
15:30 → fusion.timer → 融合引擎
       ├─ ly: 实时预测 (RandomForest)
       ├─ ml: 今日报告 (scheduler 已生成)
       ├─ at: 昨日数据 (stale)
       └─ 结果 → 企业微信推送 + JSON/CSV 存档
16:00 → TA.timer → TradingAgent 深度分析
       └─ 生成今日日志 → 供次日融合使用
```

## 输出文件

```
data/fusion_output/fusion_{date}.json     # 完整融合结果（含各系统得分）
data/fusion_output/fusion_{date}.csv      # 简洁版 CSV
config/logs/fusion-cron.log               # 融合引擎日志
config/logs/ta-cron.log                   # TradingAgent 日志
```

## 测试

```bash
.venv/bin/python -m pytest tests/test_fusion.py -v
# 88/89 passed (1 pre-existing env-dependent failure)
```
