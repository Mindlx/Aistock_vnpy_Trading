# Aistock_vnpy_Trading

三系统融合决策平台。将三个深度定制的独立股票分析系统整合为统一信号，通过加权投票机制输出最终投资建议。

---

## 目录

- [项目概述](#项目概述)
- [系统架构](#系统架构)
- [三个子系统](#三个子系统)
- [融合引擎](#融合引擎)
- [快速开始](#快速开始)
- [使用方式](#使用方式)
- [目录结构](#目录结构)
- [上游来源](#上游来源)
- [许可证](#许可证)

---

## 项目概述

Aistock_vnpy_Trading 是一个 **三系统信号融合平台**，不是单一的策略模型。核心理念是：三个底层逻辑不同的独立系统分别分析市场，当它们形成共识时信号可靠性显著提升。

**三个子系统各有侧重：**

| 系统 | 方法 | 频率 | 核心输出 |
|------|------|------|---------|
| lynx_vnpy | RandomForest 量化模型 | 日频 | 上涨概率 + 信号等级 |
| MindLynx-Aistock | 因子+策略+LLM 推理 | 日频/实时 | 综合评分 + 决策仪表盘 |
| mind_TradingAgent | 多智能体辩论（LangGraph） | 盘后 | 5级评级 + 决策摘要 |

**融合引擎** 接收三路信号，通过可配置权重线性积分，自动检测系统间分歧并施加减仓惩罚。

> ⚠️ 仅供学习和研究目的。不构成任何投资建议。市场有风险，投资需谨慎。

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                        Aistock_vnpy_Trading                  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  lynx_vnpy   │  │MindLynx-    │  │ mind_            │   │
│  │  (量化信号)   │  │Aistock      │  │ TradingAgent     │   │
│  │              │  │(AI分析)      │  │ (多智能体辩论)    │   │
│  ├──────────────┤  ├──────────────┤  ├──────────────────┤   │
│  │ RandomForest │  │ 12因子+策略  │  │ 多空辩论         │   │
│  │ 技术指标     │  │ LLM推理      │  │ 风险讨论         │   │
│  │ 上涨概率%    │  │ 评分0-100    │  │ 5级评级          │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                 │                    │             │
│         ▼                 ▼                    ▼             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                   融合引擎 (src/)                     │   │
│  │  ├─ normalizer.py   → 信号归一化                      │   │
│  │  ├─ fusion_engine.py → 加权积分 + 分歧检测 + 惩罚     │   │
│  │  ├─ data_loader.py   → 零侵入三系统数据读取            │   │
│  │  └─ wecom_notifier.py → 企业微信推送                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                              │                               │
│                              ▼                               │
│                    📊 最终决策 + 仓位建议                     │
└──────────────────────────────────────────────────────────────┘
```

---

## 三个子系统

### lynx_vnpy — 量化信号系统

**上游**: [Mindlx/lynx_vnpy](https://github.com/Mindlx/lynx_vnpy) ← [vnpy/vnpy](https://github.com/vnpy/vnpy) (MIT)

基于 RandomForest 的量化信号系统：

- **特征工程**: 15 个技术指标（RSI, MACD, ATR, 布林带, CCI 等）
- **模型**: RandomForest 分类器，预测次日涨跌方向
- **输出**: 上涨概率 + 5级信号（买入/关注/观望/谨慎/回避）
- **数据源**: 新浪财经免费 API

本项目保留 vnpy 完整库代码（alpha 因子研究框架、CTA 策略引擎、交易接口等），可供后续扩展。

### MindLynx-Aistock — AI 股票分析系统

**上游**: [Mindlx/MindLynx-Aistock](https://github.com/Mindlx/MindLynx-Aistock) ← [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) (MIT)

> 🤖 基于 AI 大模型的 A股自选股智能分析系统，因子计算 → LLM推理 → 决策仪表盘 → 多渠道推送。

- **AI 决策**: 10 因子得分 + 30+ TA指标 + Regime状态 + ATR仓位 → LLM综合判决
- **策略引擎**: 15 策略 Agent 编排（多头趋势、均线金叉、缩量回踩等），自动加权融合
- **LLM 推理**: 结合技术面 + 基本面 + 消息面，输出综合评分 0-100
- **决策仪表盘**: 核心结论、价格位置、筹码结构、作战计划

### mind_TradingAgent — 多智能体交易系统

**上游**: [Mindlx/mind_TradingAgents](https://github.com/Mindlx/mind_TradingAgents) ← [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) (Apache 2.0)

基于 LangGraph 的多智能体辩论框架：

- **分析师**: 市场 / 情绪 / 新闻 / 基本面四位分析师
- **研究**: 多头 vs 空头辩论 → 研究经理裁决
- **交易员**: 将投资计划转化为交易提案
- **风险管理**: 激进 / 保守 / 中立三方辩论
- **决策**: 投资组合经理输出 5 级评级（Buy/Overweight/Hold/Underweight/Sell）

---

## 融合引擎

融合引擎位于 `src/`，模块如下：

| 模块 | 功能 |
|------|------|
| `normalizer.py` | 三系统信号统一归一化 |
| `fusion_engine.py` | 加权积分 + 分歧检测 + 缺失容错 |
| `data_loader.py` | 零侵入读取三个系统输出 |
| `wecom_notifier.py` | 企业微信 Markdown 推送 |
| `logger.py` | CSV/JSON 持久化 |

### 融合算法

```
融合总分 = lynx得分 × 0.35 + MindLynx得分 × 0.35 + TradingAgent得分 × 0.30
```

**核心特性**:

- **分歧检测**: 系统间信号方向冲突时自动惩罚，降低仓位上限至 1成
- **置信度调制**: 置信度参与加权，低置信信号自动降权
- **缺失容错**: 任一系统不可用，权重自动重分配，标记降级
- **仓位映射**: 融合分 → 强烈看多/弱看多/中性/弱看空/强烈看空

### 决策映射

| 融合分 | 信号 | 仓位 |
|--------|------|------|
| > 0.50 | 🟢 强烈看多 | 2-3成 |
| 0.20 ~ 0.50 | 🟢 弱看多 | 0.5-1成 |
| -0.10 ~ 0.20 | ⚪ 中性/观望 | 0成 |
| -0.50 ~ -0.10 | 🔴 弱看空 | 减仓至0.5成以内 |
| < -0.50 | 🔴 强烈看空 | 清仓 |

---

## 快速开始

### 环境准备

```bash
git clone https://github.com/Mindlx/Aistock_vnpy_Trading.git
cd Aistock_vnpy_Trading
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如需运行 TradingAgent，额外安装：

```bash
pip install -e systems/mind_TradingAgent
```

### 配置

```bash
# 企业微信 Webhook（可选）
vim config/settings.yaml

# TradingAgent API Key（可选）
cp systems/mind_TradingAgent/.env.example systems/mind_TradingAgent/.env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

---

## 使用方式

### 模拟运行

```bash
python scripts/run_daily.py --mock --dry-run
```

### 融合分析

```bash
# 读取三个系统已有输出，生成融合信号
python scripts/run_daily.py

# 指定日期
python scripts/run_daily.py --date 2026-05-29
```

### 触发 TradingAgent（需 LLM API Key）

```bash
python scripts/run_daily.py --run-ta
```

### 同步上游更新

```bash
./scripts/sync_systems.sh
```

---

## 目录结构

```
Aistock_vnpy_Trading/
├── src/                  # 融合引擎
│   ├── normalizer.py     # 信号归一化
│   ├── fusion_engine.py  # 融合引擎（含分歧检测）
│   ├── data_loader.py    # 三系统数据读取
│   ├── wecom_notifier.py # 企业微信推送
│   ├── logger.py         # 日志记录
│   ├── mind_agent_wrapper.py  # TradingAgent 封装
│   └── mind_stock_config.py   # A股代码映射
├── scripts/
│   ├── run_daily.py      # 每日执行入口
│   └── sync_systems.sh   # 同步脚本
├── config/
│   ├── settings.yaml     # 融合配置
│   ├── stock_pool.csv    # 10只股票池
│   └── systems.yaml      # 路径映射
├── systems/              # 三个深度定制子系统
│   ├── lynx_vnpy/
│   ├── MindLynx-Aistock/
│   └── mind_TradingAgent/
└── tests/
```

---

## 上游来源

| 子系统（systems/ 内） | 上游（Mindlx 独立仓库） | 原始上游 | 许可证 |
|----------------------|-------------------------|---------|--------|
| lynx_vnpy | [Mindlx/lynx_vnpy](https://github.com/Mindlx/lynx_vnpy) | [vnpy/vnpy](https://github.com/vnpy/vnpy) | MIT |
| MindLynx-Aistock | [Mindlx/MindLynx-Aistock](https://github.com/Mindlx/MindLynx-Aistock) | [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | MIT |
| mind_TradingAgent | [Mindlx/mind_TradingAgents](https://github.com/Mindlx/mind_TradingAgents) | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Apache 2.0 |

详见 [NOTICE.md](NOTICE.md)。

---

## 许可证

融合引擎代码（`src/`, `scripts/`, `config/`）基于 MIT 许可证发布。
各子系统保留其原始许可证，详见各系统 `LICENSE` 文件。
