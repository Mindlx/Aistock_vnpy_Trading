# 部署指南

## 环境要求

- Python 3.10+
- pip
- （可选）企业微信机器人 Webhook（用于推送）
- （可选）DeepSeek/OpenAI API Key（用于 TradingAgent）

---

## 快速部署

### 1. 克隆项目

```bash
git clone https://github.com/Mindlx/Aistock_vnpy_Trading.git
cd Aistock_vnpy_Trading
```

### 2. 创建虚拟环境

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# 或 .venv\Scripts\activate  # Windows
```

### 3. 安装依赖

```bash
# 融合引擎基础依赖
pip install -r requirements.txt

# lynx_vnpy 依赖（如需运行信号生成）
pip install scikit-learn joblib pandas numpy requests

# mind_TradingAgent 依赖（如需运行多智能体分析）
pip install -e systems/mind_TradingAgent
```

### 4. 配置

#### 基础配置

编辑 `config/settings.yaml`：

```yaml
# 权重（可按需调整）
weights:
  lynx_vnpy: 0.35
  mindlynx: 0.35
  tradingagent: 0.30

# 企业微信 Webhook（可选）
wecom:
  webhook_url: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"
  enabled: false  # 配置好后再开启
```

#### TradingAgent API 配置（可选）

```bash
cp systems/mind_TradingAgent/.env.example systems/mind_TradingAgent/.env
```

编辑 `.env`，填入 LLM API Key：

```bash
DEEPSEEK_API_KEY=sk-your-key-here
# 或 OPENAI_API_KEY=sk-your-key-here

# 使用 DeepSeek V4 Flash（推荐，性价比高）
MIND_TRADINGAGENT_LLM_PROVIDER=deepseek
MIND_TRADINGAGENT_DEEP_THINK_LLM=deepseek-v4-flash
MIND_TRADINGAGENT_QUICK_THINK_LLM=deepseek-v4-flash
MIND_TRADINGAGENT_LLM_BACKEND_URL=https://api.deepseek.com
MIND_TRADINGAGENT_OUTPUT_LANGUAGE=Chinese
```

---

## 运行方式

### 模拟模式（验证安装）

```bash
python scripts/run_daily.py --mock --dry-run
```

输出示例：

```
📊 融合决策结果
-----------------------------------------------
有效: 10/10
降级: 2
分歧: 3
分布: {'strong_bullish': 1, 'weak_bullish': 2, 'neutral': 2, 'weak_bearish': 3, 'strong_bearish': 2}

--- 个股结果 ---
  强烈看多 皖新传媒(601801) 融合=0.65 ...
  弱看多 古麒绒材(001390) 融合=0.41 ...
  ...
```

### 日终融合

```bash
python scripts/run_daily.py
```

读取三个系统已有的输出，生成融合结果。

### 全量分析（含 TradingAgent）

```bash
python scripts/run_daily.py --run-ta
```

每只股票需要 3-5 分钟 LLM 推理，10 只约需 30-50 分钟。

---

## 定时任务

所有定时任务统一使用 **systemd timer** 管理，便于分发和管理。

### 启用全部服务

```bash
# 克隆项目后，一键启用所有 systemd 服务
systemctl --user enable --now Aistock_vnpy_Trading-scheduler.service
systemctl --user enable --now Aistock_vnpy_Trading-monitor.service  
systemctl --user enable --now Aistock_vnpy_Trading-fusion.timer
```

### 服务说明

| 服务 | 类型 | 触发时间 | 职责 |
|------|------|---------|------|
| `Aistock_vnpy_Trading-scheduler` | service | 常驻 | MindLynx 整点分析/大盘复盘/情报搜集 |
| `Aistock_vnpy_Trading-monitor` | service | 常驻 | 盘中实时监控（ATR止损/量价异动） |
| `Aistock_vnpy_Trading-fusion` | timer | 工作日 15:30 | 日终三系统融合 + 企业微信推送 |

### 查看状态

```bash
# 查看服务状态
systemctl --user status Aistock_vnpy_Trading-scheduler
systemctl --user status Aistock_vnpy_Trading-monitor

# 查看定时器状态
systemctl --user list-timers Aistock_vnpy_Trading-fusion.timer

# 查看日志
journalctl --user -u Aistock_vnpy_Trading-scheduler --no-pager -n 50
journalctl --user -u Aistock_vnpy_Trading-fusion --no-pager -n 50
```

### 停止服务

```bash
systemctl --user stop Aistock_vnpy_Trading-scheduler.service
systemctl --user stop Aistock_vnpy_Trading-monitor.service
systemctl --user stop Aistock_vnpy_Trading-fusion.timer
```

### 服务文件位置

```
~/.config/systemd/user/
├── Aistock_vnpy_Trading-scheduler.service
├── Aistock_vnpy_Trading-monitor.service
├── Aistock_vnpy_Trading-fusion.service
└── Aistock_vnpy_Trading-fusion.timer
```

---

## 系统间数据同步

### 从上游拉取子系统更新

```bash
./scripts/sync_systems.sh
```

此脚本将三个 Mindlx 仓库的最新代码同步到 `systems/` 目录。

### 同步规则

| 系统 | 排除项 | 原因 |
|------|--------|------|
| lynx_vnpy | lynx_env/, examples/, .git/ | venv 不可移植，示例代码不需要 |
| MindLynx | .venv/, logs/, docs/, data/, .env | venv + 运行时数据 + 密钥 |
| TradingAgent | .env, assets/, .git/ | 密钥 + 图片资源 |

---

## 输出文件

| 文件 | 格式 | 说明 |
|------|------|------|
| `config/logs/fusion_history.csv` | CSV | 每次融合的单行记录 |
| `config/logs/fusion_YYYYMMDD.log` | Log | 日志 |
| `data/fusion_output/fusion_YYYY-MM-DD.json` | JSON | 每日完整融合结果 |
| `data/fusion_output/fusion_YYYY-MM-DD.csv` | CSV | 每日融合摘要 |

---

## 故障排除

### lynx_vnpy 信号为空

- 检查网络能否访问 `money.finance.sina.com.cn`
- 检查 `systems/lynx_vnpy/models/` 是否有 `.pkl` 文件

### MindLynx 报告未找到

- 确认 `systems/MindLynx-Aistock/reports/` 下有 `report_*.md` 文件
- 文件名格式：`report_YYYY-MM-DD.md` 或 `report_YYYYMMDD.md`

### TradingAgent 连接错误

- 检查 `.env` 中的 API Key 是否正确
- 确认网络能访问 `api.deepseek.com`
- 模型名使用 `deepseek-v4-flash`（不要用 `deepseek-chat`）

### 系统间无信号

如果三个系统都暂时没有数据，使用 `--mock` 模式测试融合逻辑。
