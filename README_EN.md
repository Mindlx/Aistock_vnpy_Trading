# Aistock_vnpy_Trading

A three-system ensemble trading platform that fuses signals from three independently customized stock analysis subsystems into unified investment decisions.

---

## Overview

Aistock_vnpy_Trading is a **signal fusion platform**, not a single strategy model. Three subsystems with fundamentally different methodologies analyze the market independently — when they reach consensus, the signal reliability is significantly improved.

| Subsystem | Method | Frequency | Output |
|-----------|--------|-----------|--------|
| lynx_vnpy | RandomForest quantitative model | Daily | Up-probability + signal grade |
| MindLynx-Aistock | 12 factors + 15 strategies + LLM | Daily/Real-time | Composite score + action advice |
| mind_TradingAgent | Multi-agent debate (LangGraph) | Post-market | 5-tier rating + decision summary |

The **fusion engine** receives all three signals, applies configurable weighted averaging, automatically detects inter-system disagreement, and applies an uncertainty penalty.

> ⚠️ For educational and research purposes only. No investment advice implied.

---

## Architecture

```
                    ┌─────────────────────────────┐
                    │     Fusion Engine (src/)     │
                    │  normalizer → fusion_engine  │
                    │  → wecom_notifier → logger   │
                    └──────────┬──────────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
  │  lynx_vnpy   │    │MindLynx-    │    │ mind_            │
  │  (RF model)  │    │Aistock      │    │ TradingAgent     │
  │  probability │    │(AI + LLM)   │    │ (Multi-agent)    │
  │  + signal    │    │ score 0-100 │    │ 5-tier rating    │
  └──────────────┘    └──────────────┘    └──────────────────┘
```

---

## The Three Subsystems

### lynx_vnpy — Quantitative Signal Engine

**Upstream**: [vnpy/vnpy](https://github.com/vnpy/vnpy) (MIT) — heavily customized

- 15 technical indicators (RSI, MACD, ATR, Bollinger, CCI, etc.)
- RandomForest classifier predicting next-day direction
- Output: probability (0-100%) + 5-level signal (Buy/Watch/Neutral/Caution/Avoid)
- Data source: Sina Finance free API

The complete vnpy library is preserved (alpha factor research, CTA strategy engine, trading gateways) for future expansion.

### MindLynx-Aistock — AI Stock Analysis

**Upstream**: [MindLynx-Aistock](https://github.com/Mindlx/MindLynx-Aistock) (MIT) — heavily customized

- 12-factor quantitative model (momentum, volatility, volume, sentiment, etc.)
- 15 strategy framework (trend, golden cross, breakout, chan theory, etc.)
- LLM reasoning combining technical + fundamental + news analysis
- Decision dashboard with price position, chip structure, battle plan

### mind_TradingAgent — Multi-Agent Trading System

**Upstream**: [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) (Apache 2.0) — heavily customized

- LangGraph-based multi-agent debating framework
- 4 analysts (Market, Sentiment, News, Fundamentals)
- Bull vs Bear research debate → Research Manager
- Trader → Risk Management (3-way debate) → Portfolio Manager
- Output: 5-tier rating (Buy/Overweight/Hold/Underweight/Sell)

---

## Fusion Engine

Located in `src/`:

| Module | Purpose |
|--------|---------|
| `normalizer.py` | Map all signals to unified coordinate system |
| `fusion_engine.py` | Weighted integration + disagreement detection |
| `data_loader.py` | Zero-intrusion data reading from subsystems |
| `wecom_notifier.py` | WeCom (企业微信) Markdown push |
| `logger.py` | CSV/JSON persistence |

### Algorithm

```
fusion_score = lynx × 0.35 + MindLynx × 0.35 + TradingAgent × 0.30
```

### Decision Map

| Score | Signal | Position |
|-------|--------|----------|
| > 0.50 | 🟢 Strong Bullish | 20-30% |
| 0.20 ~ 0.50 | 🟢 Weak Bullish | 5-10% |
| -0.10 ~ 0.20 | ⚪ Neutral | 0% |
| -0.50 ~ -0.10 | 🔴 Weak Bearish | Reduce to <5% |
| < -0.50 | 🔴 Strong Bearish | Close all |

---

## Quick Start

```bash
git clone https://github.com/Mindlx/Aistock_vnpy_Trading.git
cd Aistock_vnpy_Trading
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For TradingAgent support:

```bash
pip install -e systems/mind_TradingAgent
```

## Usage

```bash
# Mock run (no external dependencies)
python scripts/run_daily.py --mock --dry-run

# Normal fusion
python scripts/run_daily.py

# With TradingAgent analysis (requires LLM API key)
python scripts/run_daily.py --run-ta

# Sync upstream updates
./scripts/sync_systems.sh
```

---

## Project Structure

```
Aistock_vnpy_Trading/
├── src/                  # Fusion engine
├── scripts/              # Entry points + sync
├── config/               # Configuration
├── systems/              # Three customized subsystems
│   ├── lynx_vnpy/
│   ├── MindLynx-Aistock/
│   └── mind_TradingAgent/
└── tests/
```

---

## Attribution

| Subsystem | Upstream | License |
|-----------|----------|---------|
| lynx_vnpy | [vnpy/vnpy](https://github.com/vnpy/vnpy) | MIT |
| MindLynx-Aistock | [MindLynx-Aistock](https://github.com/Mindlx/MindLynx-Aistock) | MIT |
| mind_TradingAgent | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Apache 2.0 |

See [NOTICE.md](NOTICE.md).

---

## License

Fusion engine code (`src/`, `scripts/`, `config/`) is MIT licensed.
Each subsystem retains its original license.
