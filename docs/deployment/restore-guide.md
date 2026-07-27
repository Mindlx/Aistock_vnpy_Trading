# ═══════════════════════════════════════════════════════════
# 融合系统备份恢复指南
# 创建: 2026-07-27 19:00
# ═══════════════════════════════════════════════════════════

## 备份文件

```
Aistock_vnpy_Trading_backup_20260727_1907.tar.gz  (191 MB)
```

## 恢复步骤

```bash
# 1. 解压
tar xzf Aistock_vnpy_Trading_backup_20260727_1907.tar.gz
cd Aistock_vnpy_Trading/

# 2. 运行恢复脚本
bash scripts/setup_restore.sh
```

## 备份排除项

| 排除项 | 原因 | 恢复方式 |
|--------|:----|:---------|
| `.venv/` | Python 虚拟环境 | `setup_restore.sh` 自动创建+安装依赖 |
| `__pycache__/` | Python 字节码 | 运行时自动生成 |
| `node_modules/` | Node 前端依赖 | 需要 `cd apps/dsa-web && npm install` |
| `*.pyc` | Python 字节码 | 运行时自动生成 |
| `.git/` | Git 版本控制 | 需要 `git init && git remote add origin <url>` |
| `.hermes/` | Hermes Agent 配置 | 个人环境变量 |

## 前置条件

- Python 3.11+
- Systemd (可选, 用于定时服务)

## 关键数据文件

| 文件 | 说明 | 备份中是否包含 |
|------|:-----|:--------------|
| `data/stock_analysis.db` | ML 回测数据库 | ✅ (24MB) |
| `data/backtest/bt_results.db` | 融合回测数据库 | ✅ (188KB) |
| `data/realtime/*.json` | 实时信号文件 | ✅ |
| `data/c1test/*.json` | c1test 回测报告 | ✅ |

## 已知依赖项

- **squarify/matplotlib** — 大盘复盘 treemap 图
- **weasyprint** — PDF 报告生成
- **langgraph** — AT 多Agent 执行框架
- **akshare/efinance/tushare** — A股数据源

## 部署定时器 (Systemd)

```bash
bash scripts/deploy-systemd.sh
systemctl --user list-timers --no-pager | grep Aistock
```

## 恢复后验证

```bash
# 验证依赖完整性
cd Aistock_vnpy_Trading/
.venv/bin/python scripts/c1test.py --quick

# 验证推送
.venv/bin/python -c "from src.normalizer import SignalNormalizer; print(SignalNormalizer.calibrate_score(100))"
# 预期输出: 75
```
