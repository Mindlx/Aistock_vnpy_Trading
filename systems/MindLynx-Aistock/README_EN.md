<div align="center">

# 📈 MindLynx-Aistock — AI-Powered Personal Investment Research Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Lines of Code](https://img.shields.io/badge/lines-76K-blue)](https://github.com/Mindlx/MindLynx-Aistock)
[![Production Ready](https://img.shields.io/badge/production-7%2F10-green)](https://github.com/Mindlx/MindLynx-Aistock)

> 🤖 Fully automated AI investment research: scheduled analysis → factor computation → LLM reasoning → knowledge accumulation → push notifications. A complete pipeline from data to decision for individual investors.

[**Features**](#-features) · [**Readiness**](#-production-readiness) · [**Quick Start**](#-quick-start)

</div>

## 🎯 Positioning

**Not a real-time quant system. An AI research co-pilot for individual investors.**

- Does NOT do: HFT, automated trading, low-latency execution
- Does: systematic analysis, multi-strategy fusion, knowledge accumulation, risk-first approach, readable push reports
- Best for: individual investors tracking 10-100 stocks
- There is no other tool that delivers fully automated personal AI investment research

## ✨ Features

| Layer | Capability | Description |
|------|------|------|
| **AI Decision** | LLM + Quant Factor Fusion | 10 factor scores + 30+ TA indicators + Regime state + ATR sizing → LLM verdict |
| **Strategy Engine** | 15 Strategy Agent Pipeline | Bull trend, MA crossover, volume breakout, etc., auto-weighted fusion |
| **Factor Engine** | 10 Factors, IC/IR Weighted | Calibrated on 1,658 samples, 53.7% baseline accuracy |
| **Knowledge Base** | 5-layer Context + TTL | Analysis history (7d) + announcements (30d) + fundamentals (90d) + sector (180d) + daily reports |
| **Backtest Engine** | 20d Window + Walk-Forward | Cost model (A/HK/US) + Sharpe/Calmar/Sortino analytics |
| **Risk Control** | 3-tier Defense | ATR position sizing + risk parity optimization + regime-adaptive parameters |
| **Real-time Monitor** | 3-phase Daemon | Intraday briefing (15min) + ATR alerts (3 levels) + volume/price anomaly detection |
| **Event Driven** | 14 Event Types, Auto Re-analysis | Earnings/buybacks/insider trading trigger re-analysis via free EastMoney API |
| **Notifications** | 13 Channels + Noise Control | WeChat/Feishu/Telegram/Discord/Slack/Email etc., mobile-optimized format |
| **Multi-User** | Group-based Routing | STOCK_GROUP_N + NOTIFY_N pattern, different stocks to different users |

## 📊 Production Readiness

| Dimension | Score | Notes |
|------|:---:|------|
| Core Analysis Engine | 9/10 | 295 analyses, ~98/day, stable 3 days |
| Methodology | 8/10 | ATR/Kelly/IC-IR calibrated |
| Push Reports | 10/10 | Compact mobile format + quant summary + action advice |
| Real-time Monitoring | 5/10 | HTTP fallback works, WS to fix |
| Self-evolution | 4/10 | Framework ready, backtest loop pending data |
| **Overall** | **7/10** | Production-ready for personal use |

> Details: [docs/production_readiness_report.md](docs/production_readiness_report.md)

## 🚀 Quick Start

### Prerequisites

- Python 3.10+ / DeepSeek or OpenAI-compatible API key
- Tushare Token (optional)

### Install

```bash
git clone https://github.com/Mindlx/MindLynx-Aistock.git && cd MindLynx-Aistock
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Run

```bash
python main.py --no-notify --force-run      # Full analysis
python main.py --stocks 600519,300750       # Specific stocks
python main.py --schedule                   # Scheduled mode (recommended)
python main.py --realtime-monitor           # Intraday monitoring
python main.py --event-monitor              # Event-driven analysis
python -m src.core.factor_backtest          # Factor baseline
```

Full guide: [docs/full-guide.md](docs/full-guide.md)

## 📄 License

MIT License.
