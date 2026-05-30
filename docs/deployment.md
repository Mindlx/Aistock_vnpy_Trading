# 部署指南

## 依赖安装

```bash
pip install -r requirements.txt

# lynx_vnpy 依赖（如需运行信号生成）
pip install scikit-learn joblib

# mind_TradingAgent 依赖（如需多智能体分析）
pip install -e systems/mind_TradingAgent
```

## 配置

```bash
# 企业微信 Webhook（可选）
vim config/settings.yaml

# TradingAgent API Key（可选）
cp systems/mind_TradingAgent/.env.example systems/mind_TradingAgent/.env
```

## systemd 服务管理

所有定时任务统一使用 systemd timer 管理。

### 启用全部服务

```bash
systemctl --user enable --now Aistock_vnpy_Trading-scheduler.service
systemctl --user enable --now Aistock_vnpy_Trading-monitor.service
systemctl --user enable --now Aistock_vnpy_Trading-fusion.timer
systemctl --user enable --now Aistock_vnpy_Trading-TA.timer
```

### 服务说明

| 服务 | 类型 | 触发 | 职责 |
|------|------|------|------|
| `scheduler` | service | 常驻 | 整点分析/大盘复盘/情报搜集 |
| `monitor` | service | 常驻 | 盘中实时监控 |
| `fusion` | timer | 工作日 15:30 | 日终三系统融合 + 推送 |
| `TA` | timer | 工作日 16:00 | TradingAgent 深度论证 |

### 常用命令

```bash
# 状态
systemctl --user list-timers
systemctl --user status Aistock_vnpy_Trading-scheduler

# 日志
journalctl --user -u Aistock_vnpy_Trading-fusion --no-pager -n 30

# 启停
systemctl --user stop Aistock_vnpy_Trading-scheduler.service
systemctl --user start Aistock_vnpy_Trading-fusion.timer
```

### 服务文件位置

```
~/.config/systemd/user/
├── Aistock_vnpy_Trading-scheduler.service
├── Aistock_vnpy_Trading-monitor.service
├── Aistock_vnpy_Trading-fusion.service
├── Aistock_vnpy_Trading-fusion.timer
├── Aistock_vnpy_Trading-TA.service
└── Aistock_vnpy_Trading-TA.timer
```

## 运行方式

```bash
# 模拟运行
python scripts/run_daily.py --mock --dry-run

# 日终融合
python scripts/run_daily.py

# 全量分析（含 TradingAgent）
python scripts/run_daily.py --run-ta
```

## 输出文件

| 文件 | 说明 |
|------|------|
| `config/logs/fusion_history.csv` | 每次融合记录 |
| `data/fusion_output/fusion_YYYY-MM-DD.json` | 每日融合结果 |

> ⚠️ 仅供学习参考。不构成投资建议。
