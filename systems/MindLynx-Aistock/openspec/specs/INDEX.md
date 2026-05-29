# OpenSpec Domain Specs Index

This directory contains the base specifications (specs) for MindLynx-Aistock's core domains.
Each spec defines what the system SHALL do — the requirements contract.

## P0 — Foundational (pre-filled)

| Domain | File | Scope |
|--------|------|-------|
| **Config** | `config/spec.md` | Env-var driven Config singleton, validation, hot-reload, persistence |
| **Data Provider** | `data-provider/spec.md` | 17 fetchers, priority chain, auto-failover, code normalization, WebSocket |
| **Pipeline** | `pipeline/spec.md` | StockAnalysisPipeline orchestration, two-phase execution, decision stability |
| **Agent & LLM** | `agent-llm/spec.md` | Single/multi-agent arch, LiteLLM multi-provider, strategy skills, tools |
| **Notification** | `notification/spec.md` | 13+ channels, noise control, routing, markdown-to-image, multi-user |

## P1 — Core (write when changes touch these)

| Domain | Key Source |
|--------|-----------|
| **Factor Engine** | `src/core/factor_engine.py` — 12-factor IC/IR scoring |
| **Strategies** | `strategies/*.yaml` — 15 YAML trading strategy definitions |
| **Backtest** | `src/core/backtest_engine.py` — Walk-forward with cost model |
| **Technical Indicators** | `src/core/indicators.py` — 30+ TA indicators |
| **API Layer** | `api/v1/` — FastAPI REST endpoints |
| **WebUI** | `apps/dsa-web/` — React/Next.js frontend |
| **Realtime Monitor** | `src/services/realtime_monitor.py` — Intraday 3-phase monitoring |

## P2 — Extensions (create delta specs on change)

| Domain | Key Source |
|--------|-----------|
| Event Monitor | `src/services/event_monitor.py` |
| Alert Center | `src/services/alert_service.py` |
| Bot System | `bot/` |
| Desktop App | `apps/dsa-desktop/` |
| Market Review | `src/core/market_review.py` |
| Knowledge Base | `src/core/stock_knowledge.py` |
| Search Service | `src/search_service.py` |
| Storage & DB | `src/storage.py` |
| Portfolio | `src/core/portfolio_optimizer.py` |
| CI/CD & DevOps | `.github/workflows/`, `docker/`, `scripts/` |
| Regime Classifier | `src/core/regime_classifier.py` |

## Spec Maintenance

- **P0 specs** are pre-filled — update them when core behavior changes
- **P1/P2 specs** — create using `/opsx/propose <change-name>` when you need to modify that domain
- **Small changes** (typos, parameter tweaks) — skip specs entirely, modify directly
- **Cross-domain changes** — update all affected specs
