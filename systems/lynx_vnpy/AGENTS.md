# AGENTS.md — lynx_vnpy

This is a fork of [vnpy/vnpy](https://github.com/vnpy/vnpy) — a Chinese quantitative
trading platform (VeighNa). Focus is on the core framework and the alpha ML module.

## Quick start

```bash
# Install core package (hatchling build)
pip install .

# Include alpha ML extras (scikit-learn, lightgbm, torch, polars, alphalens)
pip install .[alpha]

# Dev extras (for local development)
pip install -e .[alpha,dev]

# Non-pip prerequisite: ta-lib C library
#   Linux: install.sh (builds from source)
#   macOS: brew install ta-lib + install_osx.sh
#   Windows: install.bat (uses prebuilt wheel)
```

## CI pipeline (truth — runs on Windows, Python 3.13)

The workflow in `.github/workflows/pythonapp.yml` is authoritative:

```
pip install ruff mypy uv types-tqdm
uv pip install ta-lib==0.6.4 --index=https://pypi.vnpy.com
uv pip install -e .[alpha,dev] --system
ruff check .
mypy lynx_vnpy
uv build
```

Custom PyPI index `https://pypi.vnpy.com` is used for ta-lib prebuilt wheels.

## Code quality

- **Lint**: `ruff check .` (select B, E, F, UP, W; ignore E501)
- **Type check**: `mypy lynx_vnpy` (strict mode: untyped defs disallowed, no implicit optional)
- **mypy overrides**: `polars`, `lightgbm`, `hatchling.*`, `qrcode` → ignore_missing_imports
- **target-version**: py310 (ruff + mypy)

## Testing

```bash
pytest tests/          # alpha module tests (DataProxy, Alpha101 factors)
```

Tests are in `tests/` — not in `lynx_vnpy/`. Tests focus exclusively on the alpha module.
The test suite is small (~600 lines total). No other test suites exist.

## Project structure

```
lynx_vnpy/              # ← The installable package (hatchling builds this)
├── __init__.py         # version = "1.0.0-dev"
├── event/              # EventEngine (thread-based dispatcher)
├── trader/             # Core: MainEngine, BaseGateway, data objects, UI (PySide6), i18n, WeChat push
├── alpha/              # ML factor strategy module (4.x addition)
│   ├── dataset/        #   Factor engineering: Alpha101, Alpha158, DataProxy, expression engine
│   ├── model/          #   ML models: AlphaModel template + Lasso/LightGBM/MLP impls
│   ├── strategy/       #   AlphaStrategy templates + backtesting
│   └── lab.py          #   AlphaLab — research workflow manager
├── chart/              # K-line chart widgets (pyqtgraph-based)
└── rpc/                # ZeroMQ RPC (client/server)
```

## Architecture notes

- **Plugin-based**: Gateways (trading interfaces) and Apps (strategy modules) are separate
  pip-installable packages (vnpy_ctp, vnpy_ctastrategy, etc.) imported at runtime — not in
  this repo. The repo is only the core framework.

- **Entrypoint**: `MainEngine(EventEngine)` from `lynx_vnpy.trader.engine`. Apps register via
  `main_engine.add_app(AppClass)`, gateways via `main_engine.add_gateway(GatewayClass)`.

- **Event-driven**: EventEngine runs a background thread, dispatches typed events
  (EVENT_TICK, EVENT_ORDER, etc.) to registered handlers.

- **PySide6 GUI**: The trader UI uses PySide6 (Qt bindings). Run via `create_qapp()` +
  `MainWindow(main_engine, event_engine)`. See `examples/veighna_trader/run.py`.

- **Global settings**: Loaded from `.vntrader/vt_setting.json` at startup (gitignored).

- **i18n**: Chinese/English via `lynx_vnpy/trader/locale/`. `.mo` files are force-included
  in the wheel build. Generate with `generate_mo.bat` (Windows) from `.po` files.

- **WeChat notifications**: `lynx_vnpy.trader.wechat` implements iLink protocol for QR-code
  login + message push. Used by MainEngine.send_notification().

## Standalone custom code

- **`lynx_signal.py`** (repo root): A standalone signal generation script that fetches A-share
  stock data via `efinance`, computes technical indicators, trains RandomForest models, and
  pushes signals to WeCom (企业微信). Uses `efinance` and `joblib` — packages NOT declared
  in pyproject.toml.

- **`models/`** (repo root): Pre-trained `.pkl` model/scaler pairs consumed by
  `lynx_signal.py`. Not used by the `lynx_vnpy` package itself.

## Alpha module gotchas

- Uses **polars** (not pandas) for DataFrames — this differs from the main trader module
  which uses pandas + numpy.
- The expression engine (`calculate_by_expression`) parses math-like strings into polars
  expressions. Custom functions register via `register_functions()`.
- `AlphaDataset` requires alphalens-reloaded for factor analysis (import error if missing).
- Training data must include `datetime`, `vt_symbol`, `open`, `high`, `low`, `close`, `volume`,
  `vwap` columns.
- `lab/` directory is gitignored — per-user research artifacts are not committed.

## Version

Current: `1.0.0-dev` (fork diverged from vnpy 4.4.0). This is defined in `lynx_vnpy/__init__.py`
and consumed by hatchling via `pyproject.toml` `[tool.hatch.version]`.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **lynx_vnpy** (4249 symbols, 7361 relationships, 100 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
| `gitnexus://repo/lynx_vnpy/context` | Codebase overview, check index freshness |
| `gitnexus://repo/lynx_vnpy/clusters` | All functional areas |
| `gitnexus://repo/lynx_vnpy/processes` | All execution flows |
| `gitnexus://repo/lynx_vnpy/process/{name}` | Step-by-step execution trace |

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
