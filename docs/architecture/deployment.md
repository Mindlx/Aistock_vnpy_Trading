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
# 同步所有配置 + 启用新 timer
bash scripts/deploy-systemd.sh

# 如需同时重启常驻 daemon（配置变更后建议）
bash scripts/deploy-systemd.sh --restart-daemons
```

⚠️ **重要经验**：修改 `config/systemd/` 后必须运行此脚本，否则 systemd 仍用旧配置运行。
2026-07-22 审计发现此前多次配置更新均未部署，导致定时器时间错位、部分服务未启动。

### 服务总览

#### 常驻 Daemon（持续运行）

| Service | 运行内容 | 自动重启 |
|---------|---------|:--------:|
| `Aistock_vnpy_Trading-monitor.service` | MindLynx 盘中实时监控（Phase 1-3） | `always` |
| `Aistock_vnpy_Trading-scheduler.service` | MindLynx 定时分析调度（情报/整点/复盘） | `always` |
| `Aistock_vnpy_Trading-ml-factor.service` | ML 12 因子实时计算（每 300s） | `always` |
| `Aistock_vnpy_Trading-alpha158.service` | Alpha158 58 因子实时计算（每 300s） | `always` |
| `Aistock_vnpy_Trading-realtime-fusion.service` | 准实时融合（扫描文件交换区，每 300s） | `always` |
| `Aistock_vnpy_Trading-data-warehouse.service` | 数据仓库调度器（自动刷新 16 类数据） | `on-failure` |
| `Aistock_vnpy_Trading-webui.service` | ML WebUI 前端 (port 8000) | `always` |

#### Timer 触发（oneshot）

| Service | 触发时间 | 说明 |
|---------|---------|------|
| `fusion.timer` | 工作日 **18:00** | 日终融合决策 + 推送 |
| `fusion-eval.timer` | 工作日 **12:12 / 14:41** | 日间融合评估（只记回测，不推送） |
| `realtime-fusion.timer` | 工作日 **10:43** | 准实时融合 daemon 启动（等 AT+ML 数据就绪） |
| `TA.timer` | 工作日 **10:10 / 13:30** | TradingAgent 辩论（积累行情数据后） |
| `eastmoney-rating.timer` | 工作日 **10:53 / 13:53** | 东方财富评级（对齐 ML 整点分析） |
| `eastmoney-rating-pdf.timer` | 工作日 **18:00** | 评级 PDF 推送（60s 延时避让融合） |
| `lynx-signal.timer` | 工作日 **15:15** | LY 量化信号推送（收盘后） |
| `retrain-lgb.timer` | 工作日 **15:20** | LGB + RF 模型自动重训 |
| `warehouse-warmup.timer` | **08:30 / 09:20 / 10:50 / 12:55 / 13:50** | 数据仓库分阶段预热 |
| `alpha158.timer` | 工作日 **09:33** | Alpha158 daemon 启动 |
| `c1test-daily.timer` | 每日 **20:00** | c1test 统一回测 |
| `calibrate-alphas.timer` | 每日 **12:30** | 因子校准 |
| `diagnose-agreement.timer` | 每日 **20:30** | 融合分歧诊断 |
| `trace-collect.timer` | 每 **10 分钟** | 运行时追踪收集 |
| `eastmoney-calibrate.timer` | 每月 **1 日 10:00** | 东方财富评级校准 |
| `lynx-backtest.timer` | 周日 **10:00** | LY 周度回测 |
| `c1test-weekly.timer` | 周日 **10:30** | c1test 全量回测 |
| `ic-monitor.timer` | 周五 **19:30** | IC 监控 |

### 启用全部服务

```bash
# 1. 同步配置 + 启用所有 timer
bash scripts/deploy-systemd.sh

# 2. 启动常驻 daemon
systemctl --user start Aistock_vnpy_Trading-monitor.service
systemctl --user start Aistock_vnpy_Trading-scheduler.service
systemctl --user start Aistock_vnpy_Trading-ml-factor.service
systemctl --user start Aistock_vnpy_Trading-alpha158.service
systemctl --user start Aistock_vnpy_Trading-realtime-fusion.service
systemctl --user start Aistock_vnpy_Trading-data-warehouse.service
systemctl --user start Aistock_vnpy_Trading-webui.service
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
08:30  warehouse-warmup → 数据仓库预热（日K线）
09:00  scheduler → 每日情报盘前搜集 + 推送
09:20  warehouse-warmup → 数据仓库预热
09:33  alpha158 daemon 启动
10:10  TA 辩论（早盘） ← 积累 40min 行情数据
10:43  realtime-fusion daemon 启动（之后每 5min 扫描）
10:50  warehouse-warmup → 数据仓库预热
10:53  eastmoney-rating（对齐 11:00 ML 整点）
11:00  ML 整点分析（早盘）
12:12  fusion-eval（日间融合评估）
12:55  warehouse-warmup → 数据仓库预热
13:30  TA 辩论（午盘）
13:50  warehouse-warmup → 数据仓库预热
13:53  eastmoney-rating（对齐 14:00 ML 整点）
14:00  ML 整点分析（午盘）
14:41  fusion-eval（日间融合评估）
15:15  lynx-signal → LY 量化信号推送 + 写 ly_signal.json
15:20  retrain-lgb → LGB + RF 模型重训
18:00  fusion → 日终融合决策 + 推送 ★
18:00  eastmoney-rating-pdf → 东方财富评级PDF推送（60s 延时）
18:18  ML scheduler → 日终整点分析（原 23:59 从未执行）
```

融合系统关键依赖：

```
ly:   昨日收盘后即可生成（RF 模型不需要当天数据）
ml:   因子数据 08:40 就绪，LLM 整点分析 11:00/14:00/收盘后
at:   10:10/13:30 各跑一轮辩论
融合: 18:00 终版（所有数据就绪）
```

## 配置管理

修改 systemd 配置后：

```bash
vim config/systemd/xxx.service                       # 改配置
bash scripts/deploy-systemd.sh                       # 同步到 systemd + 启用新 timer
systemctl --user restart xxx.service                 # 重启生效
```

⚠️ **2026-07-22 审计发现**：此前多次 `config/systemd/` 修改从未真正部署到 systemd，
导致定时器时间错位（TA/fusion/eastmoney-rating 等）、部分服务未安装（融合评估/仓库预热/c1test回测）。
修改后必须运行 `bash scripts/deploy-systemd.sh` 才能生效。

验证命令：

```bash
# 检查所有 timer 时间是否与 config/ 一致
for f in config/systemd/*.timer; do
  base=$(basename "$f")
  echo "--- $base ---"
  echo "config:    $(grep OnCalendar $f)"
  echo "installed: $(systemctl --user show $base -p OnCalendar --value 2>/dev/null)"
done

# 检查所有 timer 是否已启用
systemctl --user list-timers --no-pager | grep -E 'Aistock|aistock|c1test'
```

常驻 daemon 配置变更后需重启：

```bash
bash scripts/deploy-systemd.sh --restart-daemons
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
