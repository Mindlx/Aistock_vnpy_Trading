# Aistock_vnpy_Trading

An ensemble trading system that fuses three heavily customized private forks:

- **lynx_vnpy** — vnpy fork with a RandomForest signal engine (daily frequency, technical indicators, probability-based signals)
- **MindLynx-Aistock** — multi-factor (12 factors + 15 strategies) stock analysis with LLM reasoning, real-time monitoring, and daily decision dashboard
- **mind_TradingAgent** — multi-agent debating framework (LangGraph-based) that simutes bull/bear debates, risk discussion, and produces a consensus portfolio decision

Each module independently analyzes market data and outputs a standardized signal (direction, confidence, suggested position). A weighted voting engine fuses the three results — users can assign fixed or dynamic weights (e.g., based on historical Sharpe ratio or regime detection). An uncertainty penalty is applied when modules disagree. The final combined suggestion is formatted for WeCom push and daily CSV/JSON logging.

This architecture reduces single-model bias, improves robustness through agent debate, and leverages both quantitative (RandomForest) and qualitative (LLM reasoning + multi-agent) insights.

**Key features**: modular design, zero-intrusion integration, configurable weights, disagreement detection, missing-data resilience, WeCom notifications.

> ⚠️ This project is for **educational and research purposes only**.  
> Trading stocks involves substantial risk. No investment advice is implied.  
> See `NOTICE.md` for upstream project attribution and license information.
