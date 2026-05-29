# MindLynx-Aistock — 自选股智能分析系统

AI 驱动的 A 股/港股/美股智能分析系统。15 策略 YAML 引擎 + ATR 动态止损 + 回测验证，交易时段每小时自动分析并推送。

## Governance

- `AGENTS.md` is the **canonical** AI collaboration doc. `CLAUDE.md` is a **symlink** to it — never break.
- Run `python scripts/check_ai_assets.py` after AI governance changes.
- Sync `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md`, `.claude/skills/` when AGENTS.md semantics change.
- **Never commit `.env`**. Use `.env.example` as template.
- When config semantics change, sync `.env.example` and assess impact on local runs, Docker, GitHub Actions, Web, and Desktop.

## Entrypoints

| File | Purpose |
|------|---------|
| `main.py` | CLI entrypoint — analysis, backtest, schedule, API server, realtime/event monitor |
| `server.py` | FastAPI uvicorn entrypoint (imports `api.app:app`) |
| `webui.py` | Equivalent to `python main.py --webui-only` |
| `SKILL.md` | Defines 3 agent-callable functions: `analyze_stock`, `analyze_stocks`, `market_review` |
| `api/app.py` | FastAPI app; routes at `api/v1/` |

**`main.py` calls `setup_env()` at module level** (line 34). Many internal files rely on env vars being set before other imports. Never remove this. Lazy pipeline import: `StockAnalysisPipeline` resolves on first attr access via `__getattr__` — don't import at module level.

## Structure

```
src/                    — Backend core
  config.py             — Config singleton loaded from .env (2802 lines)
  core/                 — Pipeline, market review, trading calendar, factor engine,
                          regime classifier, indicators, portfolio optimizer, backtest,
                          backtest report, factor monitor
  services/             — Analyzer, backtest, alert worker, notification, realtime/event monitor
  agent/                — Multi-agent orchestration
  llm/                  — litellm wrapper
  notification/         — Core notification logic
  notification_sender/  — Individual channel implementations
  notification_noise.py — Dedup, cooldown, quiet-hours
  notification_routing.py — Report/alert/error routing per channel
  schemas/              — Pydantic models
  repositories/         — Database access layer (SQLAlchemy)
  patches/              — EastMoney connection patch, etc.
  scheduler.py          — Schedule loop (schedule library)
  storage.py            — DB manager
  search_service.py     — Multi-provider search (Tavily, SerpAPI, SearXNG, Brave, Bocha)

data_provider/          — 17 fetchers with priority chain
  websocket_realtime.py — EastMoney push2 WebSocket (free realtime)
  websocket_realtime_integration.py — Integrates into DataFetcherManager
  tickflow_fetcher.py   — TickFlow SDK for market review enhancement
bot/                    — Chat bot (DingTalk, Feishu, Discord)
api/                    — FastAPI routes (v1/)
apps/
  dsa-web/              — Frontend (React/Next.js, Vite)
  dsa-desktop/          — Electron desktop app
strategies/             — 15 YAML trading strategy files (loaded dynamically)
docker/                 — Dockerfile, docker-compose.yml, entrypoint.sh
scripts/                — ci_gate.sh, test.sh, check_ai_assets.py, build-*.sh, build-*.ps1
docs/                   — 40+ doc files (guides, changelog, deployment, FAQ)
tests/                  — 143 pytest files with markers: unit, integration, network
```

**Pipeline split**: `pipeline.py` notification logic lives in `pipeline_notification.py`, data logic in `pipeline_data.py`.

## Commands

### Run analysis
```bash
python main.py                                            # analysis + market review + notify
python main.py --no-notify --force-run                    # skip trading day check
python main.py --stocks 600519,300750 --no-notify         # specific stocks only
python main.py --market-review --no-notify                # market review only
python main.py --dry-run                                  # fetch data only, no AI
python main.py --schedule                                 # scheduled daily mode
python main.py --serve-only                               # FastAPI server only (no auto analysis)
python main.py --serve                                    # API + auto analysis
python main.py --single-notify                            # push per-stock instead of aggregate
python main.py --check-notify                             # read-only notification diagnostics
```

### Backtest & Report
```bash
python main.py --backtest                                 # evaluate historical analyses
python main.py --backtest --backtest-code 600519          # single stock
python main.py --backtest --backtest-force                # re-run even if results exist
python main.py --backtest-report                          # generate report from existing results
```

### Monitor (intraday / event)
```bash
python main.py --realtime-monitor                         # WebSocket + ATR + volume alerts
python main.py --realtime-monitor-daemon                  # auto-follows A-share trading session
python main.py --event-monitor                            # announcements + investor Q&A
python main.py --event-monitor-daemon                     # periodic check with auto-reanalysis
```

### Validation
```bash
./scripts/ci_gate.sh                 # syntax + flake8 + deterministic + offline tests
./scripts/ci_gate.sh syntax          # python -m py_compile on key files
./scripts/ci_gate.sh flake8          # E9/F63/F7/F82 only
./scripts/ci_gate.sh deterministic   # ./scripts/test.sh code + yfinance
./scripts/ci_gate.sh offline-tests   # pytest -m "not network"
```

### Test
```bash
./scripts/test.sh quick    # 600519, no market review, no notify
./scripts/test.sh market   # market review only
./scripts/test.sh us-stock # AAPL
./scripts/test.sh code     # stock code recognition logic
./scripts/test.sh yfinance # yfinance code conversion
./scripts/test.sh all      # syntax + code + yfinance + flake8 + dry-run + quick
```
Full list: `./scripts/test.sh help`.

## Conventions

### Code style
- **Ruff** is the primary linter (configured in `pyproject.toml`): `E,W,F,I,N,UP,B,SIM,T20`. Ignores E501, E402, E731.
- **Black** with `line-length=120`, isort with `profile=black` (also in `setup.cfg`).
- **Flake8** (CI only): `E9,F63,F7,F82` — plus the above ignores from `setup.cfg`.
- **mypy** in `pyproject.toml`: strict=false, `check_untyped_defs=true`, `ignore_missing_imports=true`.
- **Pyright** in `pyrightconfig.json`: basic mode, `.venv` as venv.
- **pre-commit**: ruff (fix+format), black, trailing-whitespace, end-of-file-fixer, check-yaml, check-merge-conflict, detect-private-key.
- **Bandit**: skip B101 (assert in tests), exclude tests dir.
- Pytest markers: `unit`, `integration`, `network` (in `setup.cfg`). Offline: `pytest -m "not network"`. `testpaths = .`, files `test_*.py`, functions `test_*`.

### Git
- Commit messages in **English**, concise, descriptive.
- Auto-tag via GH Actions: `#patch`, `#minor`, `#major` in commit message (opt-in, only on push to main).
- PR titles: `<type>: <change summary>` — `fix`/`feat`/`refactor`/`docs`/`chore`/`test`/`ci`. No tool/agent source prefixes.
- No `git commit`/`git push` without explicit user confirmation.
- CHANGELOG `[Unreleased]` section: **flat format** — `- [type] description` per line. No category headers.

### Config
- **All config is env-var driven** (`.env`). No hardcoded secrets, ports, model names, or absolute paths.
- Key env vars in `.env.example` (765 lines). Sync it on every config change.
- Config loaded at import time via `setup_env()` in `src/config.py`. Singleton via `get_config()` or `Config.reset_instance()`.
- Scheduled runs reload `.env` each cycle via `_reload_env_file_values_preserving_overrides()` + `Config.reset_instance()`.
- `SCHEDULE_TIME` default is `"18:00"` — even when cleared in WebUI, falls back to system default.
- `WEBUI_AUTO_BUILD=true` by default — runs `npm install && npm run build` on server start.
- `WEBUI_HOST`/`WEBUI_PORT` backward-compat env vars map to `--host`/`--port`.
- `BACKTEST_REPORT_ENABLED=true` auto-generates Markdown report after each backtest.
- `REPORT_TYPE=full` → detailed report with battle plan, checklist, ATR stop-loss.
- `REPORT_INTEGRITY_ENABLED=true` by default, `REPORT_INTEGRITY_RETRY=1`.
- `SQLITE_WAL_ENABLED=true` by default.
- `PREFETCH_REALTIME_QUOTES=true` — disable if hitting rate limits.

### Stock codes
- A-share: `600519`, `000001`, `300750` (numeric, no prefix)
- HK: `hk00700`, `HK09988` (case-insensitive `hk` prefix)
- US: `AAPL`, `BRK.B` (no prefix, alphabetic)
- Normalize via `canonical_stock_code()` from `data_provider.base`
- `--force-run` skips trading-day check (default: enabled)
- YFinance code conversion: 600519→600519.SS, 000001→000001.SZ, hk00700→0700.HK

### Data providers
Priority chain (configurable via `*_PRIORITY` env vars):
- Realtime: `tencent` → `akshare_sina` → `efinance` → `akshare_em`
- Daily K-line: `efinance` (P0) → `akshare` (P1) → `tushare`/`pytdx` (P2) → `baostock` (P3) → `yfinance` (P4) → `longbridge` (P5) → TickFlow
- WebSocket: EastMoney push2 — A-share only (pure 6-digit codes), defaults off (`WEBSOCKET_REALTIME_ENABLED=false`), fails back to HTTP polling.
- Tushare free-tier: `trade_cal` needs fallback, `cyq_chips` needs rate-limit protection.
- `ENABLE_EASTMONEY_PATCH` injects NID token + random UA to reduce rate limiting.

### Analysis modes
- `AGENT_SKILLS=all` → all 15 strategy skills enabled (default: `bull_trend`)
- `AGENT_SKILL_AUTOWEIGHT=true` → auto-weight by backtest performance
- `AGENT_ARCH=single` (default) vs `AGENT_ARCH=multi`
- `AGENT_ORCHESTRATOR_MODE` = `quick`/`standard`/`full`/`specialist`
- Factor Monitor (`FACTOR_MONITOR_ENABLED=true`): tracks IC/IR for 5 core factors at `reports/factors/`.
- Feishu doc generation runs after analysis if `FEISHU_APP_ID` + `FEISHU_FOLDER_TOKEN` configured.

### Notification
13+ channels simultaneously. Supported: WeCom, Feishu webhook+stream, Telegram, Email, Pushover, ntfy, Gotify, PushPlus, Discord (webhook+bot), Slack (bot+webhook), Server酱3, AstrBot, Custom webhook.
- Routing: `NOTIFICATION_REPORT_CHANNELS`, `NOTIFICATION_ALERT_CHANNELS`, `NOTIFICATION_SYSTEM_ERROR_CHANNELS`
- Noise control: `NOTIFICATION_DEDUP_TTL_SECONDS`, `NOTIFICATION_COOLDOWN_SECONDS`, `NOTIFICATION_QUIET_HOURS`
- `MARKDOWN_TO_IMAGE_CHANNELS` — converts reports to images for Telegram/WeChat/Email (requires `wkhtmltopdf`)

### Docker
```bash
docker compose -f docker/docker-compose.yml up -d analyzer   # scheduled mode
docker compose -f docker/docker-compose.yml up -d server     # API mode
```
- Multi-stage: Node 20 slim → Python 3.11 slim-bookworm (bookworm needed for wkhtmltopdf).
- Entrypoint auto-fixes bind-mount permissions, drops to `dsa` user (UID 1000).
- `--webui` maps to `--serve` for backward compat.
- HEALTHCHECK pings `/api/health` or `/health`.

### LLM / AI
- Runtime: `litellm>=1.80.10,!=1.82.7,!=1.82.8,<2.0.0`
- Single key or `LLM_CHANNELS` multi-provider with fallback (10+ supported providers).
- Agent model: separate `AGENT_LITELLM_MODEL`, inherits main if unset.
- Temperature auto-adjusted per model (o-series omits, Kimi K2.6 uses 1.0/0.6).
- Vision model: `VISION_MODEL` / `VISION_PROVIDER_PRIORITY`.
- CI uses `LITELLM_CONFIG_YAML` written to `LITELLM_CONFIG` path at runtime.
- LiteLLM YAML config: `LITELLM_CONFIG` env var path → `LITELLM_CONFIG_YAML` contents.

### GitHub Actions
- `ci.yml`: PR validation — ai-governance → backend-gate (syntax+flake8+deterministic+offline-tests) → docker-build, plus web-gate on frontend changes.
- `daily_analysis.yml`: M-F 18:00 CST, manual dispatch with `full`/`market-only`/`stocks-only`.
- CI uses `vars.* || secrets.*` fallback pattern for every env var.
- Docker smoke test verifies imports: config, storage, notification, data_provider, analyzer, patch, bot, api.
- Other workflows: `auto-tag`, `create-release`, `desktop-release`, `docker-publish`, `pr-review`, `network-smoke`, `stale`, `ghcr-dockerhub`.

### AI asset governance (enforced by `scripts/check_ai_assets.py`)
- `CLAUDE.md` must be symlink → `AGENTS.md`
- `.github/copilot-instructions.md` must reference AGENTS.md as canonical
- `.github/instructions/` must contain: `backend.instructions.md`, `client.instructions.md`, `governance.instructions.md`
- `.claude/skills/` must contain: `README.md`, `analyze-issue/SKILL.md`, `analyze-pr/SKILL.md`, `fix-issue/SKILL.md`
- `.gitignore` must have `.claude/*` + `!.claude/skills/` + `!.claude/skills/**`

## Gotchas

- **`main.py` calls `setup_env()` at module level** (line 34). Many files depend on env vars being set before imports. Never remove or reorder.
- **StockAnalysisPipeline is lazy**: resolves via `__getattr__` — don't `from main import StockAnalysisPipeline` at module level. Use `main._get_stock_analysis_pipeline()` or the descriptor.
- **Scheduled runs**: `scheduled_task()` resolves `stock_codes=None` → triggers `config.refresh_stock_list()`. Each cycle reloads `.env` fresh.
- **Data provider failures**: Individual provider failures degrade gracefully — single stock failure doesn't block others.
- **`--check-notify`**: read-only notification diagnostics, doesn't send.
- **`--webui` flag**: maps to `--serve` (which runs API + analysis). `--webui-only` maps to `--serve-only` (API only, no analysis).
- **Chip distribution**: `ENABLE_CHIP_DISTRIBUTION=false` by default — cloud API is unstable.
- **Portfolio FX**: `PORTFOLIO_FX_UPDATE_ENABLED=true` for multi-currency exchange rate updates.
- **`AGENT_EVENT_MONITOR_ENABLED`**: runs `AlertWorker` in background during schedule mode, separate from `--event-monitor`.
- **Strategies** (`strategies/`): YAML files loaded dynamically. Called "skills" in config (`AGENT_SKILLS`), "策略" to users. Format documented in `strategies/README.md`.
- **Bot streams**: DingTalk stream (`pip install dingtalk-stream`) and Feishu stream (`pip install lark-oapi`) start in background when configured.
- **Windows build scripts** exist under `scripts/` as `.ps1` files — the project supports cross-platform desktop builds.
- **`tests/`**: 143 test files. Network tests run separately via `network-smoke.yml` workflow (non-blocking).

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **MindLynx-Aistock** (25200 symbols, 49207 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/MindLynx-Aistock/context` | Codebase overview, check index freshness |
| `gitnexus://repo/MindLynx-Aistock/clusters` | All functional areas |
| `gitnexus://repo/MindLynx-Aistock/processes` | All execution flows |
| `gitnexus://repo/MindLynx-Aistock/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## ⚡ Change Safety Checklist

Before modifying any of the following modules, **MUST** run `gitnexus_impact` and review the blast radius:

| Change Domain | Impact Surface | Pre-Change Check |
|---------------|:-------------:|------------------|
| `src/config.py:Config` field add/delete/rename | 99 files (72 src + 27 tests) | `gitnexus_impact(target="Config", direction="upstream")` |
| `src/config.py:Config` validation logic | All notification senders (13), data fetchers (8), API endpoints (4) | `gitnexus_impact(target="Config", direction="downstream")` then review d=1 |
| `src/config_llm.py:LLMConfig` / `src/config_notification.py:NotificationConfig` etc. | Config inheritance chain (stubs for future field extraction) | Verify `Config(LLMConfig, NotificationConfig, DataConfig, AnalysisConfig)` hierarchy is intact |
| `main.py` `setup_env()` position/order | 3 entry points (`main.py`, `server.py`, `webui.py`) | Verify all 3 call `setup_env` at module level before any Config import |
| `src/notification.py:NotificationService` interface | 13 notification sender implementations | Check each sender at `src/notification_sender/*.py` for signature compatibility |
| `strategies/*.yaml` field schema change | Agent skill system (`src/agent/skills/`) | Review `strategies/README.md` for the field contract |
| `data_provider/base.py` provider interface | 8+ fetcher implementations | Check each fetcher's `fetch()` method signature |
| New module-level `from main import` | Circular import risk | Check `main.py` lazy-descriptor pattern; prefer `import src.core.pipeline` directly |

**Key metrics from impact analysis audit (2026-05-24):**

```
src/config.py:Config      → 99 direct importers, HIGH risk
src/config.py:setup_env   →  6 direct callers, 20 affected processes, MEDIUM risk
src/notification_sender/  → 13/13 files import Config (100% coupling)
Lazy pipeline descriptor  → Confirmed safe (no module-level get_config outside server.py)
```

These metrics were verified via cross-check: GitNexus graph + actual code grep. Re-run `gitnexus analyze` then `gitnexus_impact` after any bulk refactor to refresh.

## Future Roadmap

### Agent 架构升级：single → multi specialist

**当前状态**: `AGENT_ARCH=single` — 15 策略作为 prompt 文本注入，1 次 LLM 调用。

**目标状态**: `AGENT_ARCH=multi` + `AGENT_ORCHESTRATOR_MODE=specialist` — 每个策略独立 `SkillAgent`，独立 LLM 推理，可追踪单策略表现。

**切换条件**（全部满足时提醒用户）:
```
✅ analysis_history ≥ 500 条
✅ backtest_results ≥ 100 条
✅ 每个策略 ≥ 30 次独立评估
```

**切换步骤**:
```env
AGENT_ARCH=multi
AGENT_ORCHESTRATOR_MODE=specialist
AGENT_SKILL_MAX_CONCURRENT=5
AGENT_SKILL_AUTOWEIGHT=true
```

**回退方案**: `AGENT_ARCH=single` 即可恢复当前模式。
