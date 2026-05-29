<div align="center">

# 📈 MindLynx-Aistock — 个人AI投研助手

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Lines of Code](https://img.shields.io/badge/lines-76K-blue)](https://github.com/Mindlx/MindLynx-Aistock)
[![Production Ready](https://img.shields.io/badge/production-7%2F10-green)](https://github.com/Mindlx/MindLynx-Aistock)

> 🤖 全自动AI投研系统：定时分析 → 因子计算 → LLM推理 → 知识积累 → 微信推送。覆盖数据获取到决策推送的完整链路，让个人投资者享受机构级投研支持。

[**功能特性**](#-功能特性) · [**就绪度**](#-生产就绪度) · [**快速开始**](#-快速开始)

</div>

## 🎯 定位

**不是实时量化系统。是个人AI投研副驾。**

- 不做高频、不做自动交易、不做低延迟
- 做：系统化分析、多策略融合、知识积累、风控前置、可读推送
- 适合：个人投资者，覆盖 10-100 只自选股
- 市面上没有第二个工具能做到全自动个人AI投研

## ✨ 功能特性

| 层级 | 能力 | 说明 |
|------|------|------|
| **AI 决策** | LLM + 量化因子融合 | 10 因子得分 + 30+ TA指标 + Regime状态 + ATR仓位 → LLM综合判决 |
| **策略引擎** | 15 策略 Agent 编排 | 多头趋势、均线金叉、缩量回踩等，自动加权融合 |
| **因子引擎** | 10 因子 IC/IR 加权 | 1658样本校准，53.7% 基线准确率 |
| **知识库** | 5层上下文 + TTL管理 | 分析历史(7d) + 权威公告(30d) + 基本面(90d) + 板块(180d) + 日报 |
| **回测引擎** | 20天窗口 + Walk-Forward | 交易成本模型(A/HK/US) + Sharpe/Calmar/Sortino 绩效分析 |
| **风险控制** | 三层风控 | ATR动态仓位 + 风险平价组合优化 + Regime自适应参数 |
| **实时监控** | 3 阶段守护进程 | 盘中简报(15min) + ATR止损(3级) + 量价异动告警 |
| **事件驱动** | 14 类事件自动重分析 | 财报/回购/减持/重组触发，东方财富+互动易免费源 |
| **推送通知** | 13 通道 + 降噪路由 | 微信/飞书/Telegram/Discord等，紧凑移动端格式 |
| **多用户** | 分组推送 | STOCK_GROUP_N + NOTIFY_N 模式，不同用户不同股票不同渠道 |

## 📊 生产就绪度

| 维度 | 评分 | 说明 |
|------|:---:|------|
| 核心分析引擎 | 9/10 | 295条分析历史，日均98条，连续3天稳定 |
| 分析方法论 | 8/10 | ATR/Kelly/IC-IR校准扎实 |
| 推送体验 | 10/10 | 紧凑格式 + 量化摘要 + 操作建议 |
| 盘中监控 | 5/10 | HTTP降级可用，WS待修复 |
| 自我进化 | 4/10 | 框架完备，回测闭环待数据积累 |
| **综合** | **7/10** | 个人使用级生产可用 |

## 🚀 快速开始

### 前置条件

- Python 3.10+ / DeepSeek 或 OpenAI 兼容 API Key
- Tushare Token（可选）

### 安装

```bash
git clone https://github.com/Mindlx/MindLynx-Aistock.git && cd MindLynx-Aistock
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 运行

```bash
python main.py --no-notify --force-run      # 全量分析
python main.py --stocks 600519,300750       # 指定股票
python main.py --schedule                   # 定时任务（推荐）
python main.py --realtime-monitor           # 盘中实时监控
python main.py --event-monitor              # 事件驱动分析
python -m src.core.factor_backtest          # 因子回测基线
```

详细配置见 [完整配置指南](docs/full-guide.md)。

## 📄 许可证

MIT License。
