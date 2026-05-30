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
| `normalizer.py` | 三系统信号统一归一化（含概率空间映射） |
| `fusion_engine.py` | 加权积分 + 分歧检测 + 缺失容错 + 贝叶斯融合 |
| `reliability.py` | 可靠性调制配置、置信度校准、幻觉检测 |
| `data_loader.py` | 零侵入读取三个系统输出 + 辩论记录解析 |
| `wecom_notifier.py` | 企业微信 Markdown 推送 |
| `unified_cache.py` | SQLite 共享 OHLCV 缓存层 |
| `logger.py` | CSV/JSON 持久化 |

### 融合算法 — 7 级语义对齐 (v3.0)

三个系统的评判参考系完全不同，采用 **7 级统一决策空间 (L7)** 进行语义对齐：

| 系统 | 参考系 | 本质 | 映射方式 |
|------|--------|------|---------|
| **ly** | 置信度 | "涨的概率？" | logit+tanh 连续映射 (保持概率信息) |
| **ml** | 操作建议 | "怎么做？" | 类别+评分微调 (持有→中性) |
| **at** | 研究评级 | "值得投吗？" | 直接 L7 映射 (纯分类) |

**线性模式**（默认）:
```
融合得分 = ly×0.35 + ml×0.35 + at×0.30   (得分范围 [-3, +3])
- 分歧检测: 方向冲突时扣减 0~2.0 分 + 仓位上限1成
- 缺失容错: 权重重分配 + 降级标记
```

**贝叶斯模式**:
```
有效权重 = α × c × (1-h)
P_fused = Σ(w_eff × P_系统) / Σ(w_eff)
- α: 系统基础可靠度 (ly=0.75, ml=0.55, at=0.40)
- c: 置信度校准 (各自独立公式)
- h: 幻觉检测 (ml:因子偏差, at:辩论一致性)
- 数学否决权: ly 强信号时覆盖融合结果
```

### 7 级决策映射

| L7 | 得分范围 | 信号 | 仓位 | 说明 |
|----|---------|------|------|------|
| +3 | [2.0, 3.0] | 🟢 强烈看多 | 2-3成 | 三系统强共识 |
| +2 | [1.0, 2.0) | 🟢 看多 | 1-2成 | 明确看多 |
| +1 | [0.33, 1.0) | 🟢 谨慎看多 | 0.5-1成 | 弱信号，需确认 |
| 0 | (-0.33, 0.33) | ⚪ 中性/持有 | 0成 | **含"持有"语义** |
| -1 | (-1.0, -0.33] | 🔴 谨慎看空 | 减至0.5成 | 弱警告 |
| -2 | (-2.0, -1.0] | 🔴 看空 | 大幅减仓 | 明确看空 |
| -3 | [-3.0, -2.0] | 🔴 强烈看空 | 清仓 | 三系统强共识 |

> **关键修正**: ml 的"持有"不再映射为看多信号，而是 L7=0 (中性)。"持有"="继续持有不动"，不是买入信号。

### 各系统映射规则

**ly** — logit+tanh 连续映射（保留 prob_up 概率信息）:
```python
score = 3.0 × tanh( ln(prob_up / (100-prob_up)) / 2.0 )
# prob_up=50 → 0.0, prob_up=70 → 1.2, prob_up=85 → 2.1
```

**ml** — 类别+评分微调:
| 建议 | 基础 L7 | 评分微调 | 语义 |
|------|---------|---------|------|
| 买入 | +2.2 | ±0.4×(S-50)/50 | 强看多 |
| 加仓 | +1.3 | ±0.4×(S-50)/50 | 看多 |
| **持有** | **0.0** | ±0.3×(S-50)/50 | **中性！** |
| 观望 | -0.1 | ±0.3×(S-50)/50 | 中性偏观望 |
| 减仓 | -1.7 | ±0.4×(S-50)/50 | 看空 |
| 卖出 | -2.1 | ±0.4×(S-50)/50 | 强看空 |

**at** — 直接 L7 映射:
```python
Buy→+2.3  Overweight→+1.3  Hold→0.0  Underweight→-1.3  Sell→-2.3
```

---

## 快速开始

### 环境准备

```bash
git clone https://github.com/Mindlx/Aistock_vnpy_Trading.git
cd Aistock_vnpy_Trading
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# MindLynx 子系统独立环境（可选，如不需 ML 可不装）
systems/MindLynx-Aistock/.venv/bin/pip install -r systems/MindLynx-Aistock/requirements.txt
```

> TA (TradingAgent) 依赖已内置在 fusion `.venv` 中，无需单独安装。
> ly (lynx_vnpy) 依赖也已在 fusion `.venv` 中。

### 配置

```bash
# 企业微信 Webhook（已预置，无需额外配置）
vim config/settings.yaml
#   wecom.enabled: true  # 已启用

# TradingAgent API Key（已配置）
cat systems/mind_TradingAgent/.env

# MindLynx API Key（已配置）
cat systems/MindLynx-Aistock/.env
```

---

## 使用方式

### 融合分析

```bash
# 默认模式（linear: 线性加权+分歧检测）
.venv/bin/python scripts/run_daily.py

# 贝叶斯模式
.venv/bin/python scripts/run_daily.py --mode bayesian

# 双模式对比
.venv/bin/python scripts/run_daily.py --mode dual

# 指定日期回测
.venv/bin/python scripts/run_daily.py --date 2026-05-29

# 仅打印不推送
.venv/bin/python scripts/run_daily.py --dry-run
```

### 模拟运行

```bash
.venv/bin/python scripts/run_daily.py --mock --dry-run
```

### 触发 TradingAgent（每日 16:00 自动执行）

```bash
.venv/bin/python scripts/run_daily.py --run-ta
```

### systemd 自动运行

所有定时任务通过 systemd 管理：

| 定时器 | 时间 | 功能 |
|--------|------|------|
| `fusion.timer` | 工作日 15:30 | 日终融合分析 |
| `TA.timer` | 工作日 16:00 | TradingAgent 深度论证 |

```bash
# MindLynx daemon（已运行）
systemctl --user start Aistock_vnpy_Trading-monitor.service
systemctl --user start Aistock_vnpy_Trading-scheduler.service

# 查看所有服务
systemctl --user list-units --all | grep aistock
```

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
