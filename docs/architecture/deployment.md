# 部署指南

## 环境要求

- Python >= 3.13
- OS: Linux（建议 Ubuntu 22.04+）
- systemd（用户级 service 管理）

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

所有 service/timer 文件统一管理在 `config/systemd/`，部署到 `~/.config/systemd/user/` 运行。

### 部署命令

```bash
bash scripts/deploy-systemd.sh
```

### 服务总览

| Service | 类型 | 运行内容 | 触发方式 |
|---------|------|---------|---------|
| `Aistock_vnpy_Trading-monitor.service` | `simple` | MindLynx 盘中实时监控 | 手动启动，daemon |
| `Aistock_vnpy_Trading-scheduler.service` | `simple` | MindLynx 定时分析调度 | 手动启动，daemon |
| `Aistock_vnpy_Trading-ml-factor.service` | `simple` | 因子层实时计算（每5分钟） | 手动启动，daemon |
| `Aistock_vnpy_Trading-realtime-fusion.service` | `simple` | 准实时融合（文件交换驱动） | 手动启动，daemon |
| `Aistock_vnpy_Trading-lynx-signal.service` | `oneshot` | lynx 量化信号推送 | timer → 工作日 15:15 |
| `Aistock_vnpy_Trading-fusion.service` | `oneshot` | 融合引擎日终分析（含TA） | timer → 工作日 19:00 |
| `Aistock_vnpy_Trading-TA.service` | `oneshot` | TradingAgent 深度论证 | timer → 工作日 09:00/13:00 |

### 启用全部服务

```bash
# 1. 同步配置
bash scripts/deploy-systemd.sh

# 2. 启动 daemon（一次启动，之后自动重启）
systemctl --user start Aistock_vnpy_Trading-monitor.service
systemctl --user start Aistock_vnpy_Trading-scheduler.service
systemctl --user start Aistock_vnpy_Trading-ml-factor.service
systemctl --user start Aistock_vnpy_Trading-realtime-fusion.service

# 3. 启用定时器（周一至周五自动触发）
systemctl --user enable --now Aistock_vnpy_Trading-fusion.timer
systemctl --user enable --now Aistock_vnpy_Trading-TA.timer
systemctl --user enable --now Aistock_vnpy_Trading-lynx-signal.timer
```

### 查看状态

```bash
# 所有服务状态
systemctl --user list-units --all | grep aistock

# 定时器下次触发时间
systemctl --user list-timers --no-pager | grep aistock

# 查看日志
journalctl --user -u Aistock_vnpy_Trading-fusion.service --since today
tail -f config/logs/fusion-cron.log        # 融合日志
tail -f config/logs/ta-cron.log            # TA 日志
tail -f config/logs/lynx-signal.log        # ly 推送日志
tail -f config/logs/realtime-fusion.log    # 实时融合日志
```

### 日志文件路径

| 日志 | 路径 |
|------|------|
| 融合引擎 | `config/logs/fusion-cron.log` |
| TradingAgent | `config/logs/ta-cron.log` |
| ly 量化信号 | `config/logs/lynx-signal.log` |
| 盘中实时融合 | `config/logs/realtime-fusion.log` |
| ML 因子服务 | `config/logs/ml-factor.log` |

## 交易日自动运行流程

```
09:00  TA.timer    → TA 分析 + 融合推送（盘中参考）
13:00  TA.timer    → TA 分析 + 融合推送（午盘更新）
15:15  lynx-signal → ly RF 量化信号推送 + 写 ly_signal.json
15:00~ scheduler   → ML 整点分析（含因子计算）
       realtime    → 实时融合 300s 扫描 exchange area（有变化才推）
19:00  fusion      → TA（含收盘全量数据）+ 融合 → 终版推送 ★
```

融合系统关键依赖：

```
ly: 昨日收盘后即可生成（RF 模型不需要当天数据）
ml: 15:00 收盘后生成完整报告
at: TA 19:00 内部自跑（使用当日 ly+ml 数据）
```

## 配置管理

修改 systemd 配置后：

```bash
vim config/systemd/Aistock_vnpy_Trading-*.service   # 改配置
bash scripts/deploy-systemd.sh                       # 同步到 systemd
systemctl --user restart xxx.service                 # 生效
```

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

## 输出文件

```
data/fusion_output/fusion_{date}.json          # 完整融合结果（含各系统得分）
data/fusion_output/fusion_{date}.csv           # 简洁版 CSV
data/realtime/ly_signal.json                   # ly 信号（准实时文件交换）
data/realtime/ml_signal.json                   # ML 因子得分（准实时文件交换）
data/realtime/at_signal.json                   # TA 评级（准实时文件交换）
```

## 测试

```bash
.venv/bin/python -m pytest tests/test_fusion.py -v
```

## 故障排查

### ML 子系统所有服务启动失败（scheduler/monitor 反复重启）

**症状**: `systemctl --user status Aistock_vnpy_Trading-scheduler.service` 显示 `activating` + 重启计数器持续增长。推送消息全天无更新。

**根因**: 执行 `sync_systems.sh` 后，ML 子系统目录的 `.venv` 被 rsync --delete 移除（该脚本会排除 `.venv/` 但会删除原目录）。systemd 服务指向的 Python 解释器路径 `systems/MindLynx-Aistock/.venv/bin/python` 不存在。

**修复**:
```bash
# 建立软链接到 fork 仓库的 .venv（fork 仓库与 sync 源路径需一致）
ln -sf /path/to/MindLynx-Aistock/.venv systems/MindLynx-Aistock/.venv

# 重启服务
systemctl --user restart Aistock_vnpy_Trading-scheduler.service Aistock_vnpy_Trading-monitor.service
```

**预防**: `sync_systems.sh` 的 `--exclude='.venv/'` 只能阻止同步时覆盖，但 `--delete` 会删除目标端不存在于源端的文件。已将 `.venv/` 加入排除列表。如果 fork 仓库的父路径改变，需要重新建立软链接。

### 推送消息无更新

1. 检查服务状态：`systemctl --user list-units --all | grep aistock`
2. 检查 ML .venv 是否存在（见上一条）
3. 检查信号文件新鲜度：`ls -la data/realtime/*.json`
4. 检查 webhook 配置：`.env` 中 `WECOM_WEBHOOK_URL` 是否正确

## 硬件需求

### 生产环境（当前配置）

| 资源 | 需求 | 说明 |
|------|------|------|
| CPU | 4 核+ | 主要为 ML 子系统的 LLM 推理 |
| 内存 | **4 GB+** | 4 个守护进程合计 ~370MB，留余量给 LLM |
| 磁盘 | **10 GB+** | Fusion venv ~1.3GB, ML venv ~1GB |
| OS | Linux (systemd) | 依赖 systemd timer 调度 |

### 低配 VPS / 分发场景

| 资源 | 最低要求 | 说明 |
|------|---------|------|
| 内存 | **2 GB** | 仅运行融合核心（关闭 monitor 常驻服务） |
| 磁盘 | **5 GB** | Fusion venv 不含 GPU 包(~1.3GB)，ML venv ~1GB |
| OS | Linux | 可用 cron 替代 systemd timer |

> Fusion venv 不含 GPU 包（nvidia/torch/triton），安装时注意避免拉入 GPU 依赖。
