## Context

`src/config.py:Config` is a 2802-line @dataclass with 99 direct importers spanning all modules. It was built incrementally — fields were added organically as features grew, with no internal grouping. The class's `_load_from_env()` constructor (lines 992-1749) is a single 750-line method that reads every env var sequentially.

Current structure:
```
src/config.py (2802 lines)
  ├── setup_env()          — dotenv loading (line 500)
  ├── Config.validate()    — validation (line 2721)
  ├── Config._load_from_env()  — massive constructor (line 992-1749)
  ├── get_config()         — singleton accessor (line 2750)
  └── 99 importers         — every module imports Config/get_config
```

The recent audit found: no thread safety in singleton, auto-validate not called at init, and no internal organization.

## Goals / Non-Goals

**Goals:**
- Split Config into 5 domain sub-modules under `src/config/`
- Zero breaking changes: `get_config()` returns identical type, all field names preserved
- Implement thread safety (lock) and auto-validate as part of the split
- Update `config_registry.py` to reflect new module structure

**Non-Goals:**
- Do NOT change any field names, types, or default values
- Do NOT change the env var loading order or priority logic
- Do NOT split the massive constructor — keep it as-is for now (deferred to future refactor)
- Do NOT change any importer code
- Do NOT change `.env.example` format

## Decisions

### Decision 1: Dataclass inheritance over composition
**Chosen**: Python dataclass inheritance with mixin pattern. Each sub-module defines a dataclass with its domain fields. `Config` inherits from all sub-classes.

**Rationale**: Existing importers use `config.field_name` access and type-check `isinstance(x, Config)`. Inheritance preserves both. Composition (`config.llm.temperature`) would break all importers.

**Alternative considered**: Composition with `__getattr__` delegation. Rejected because it breaks `isinstance` checks, IDE autocomplete, and type annotations.

### Decision 2: Keep single `_load_from_env()` for now
**Chosen**: Defer constructor split to a follow-up change. Keep the 750-line constructor intact.

**Rationale**: Splitting the constructor is far higher risk — field initialization order matters for env var parsing. Deferring allows proving the module structure works first.

### Decision 3: Module layout
```
src/config/
├── __init__.py       — re-exports Config, get_config, setup_env (backward compat)
├── base.py           — Config class skeleton, singleton, validate, _load_from_env
├── llm.py            — LLM fields (channels, models, temperature, max_tokens)
├── notification.py   — Notification fields (channel URLs, routing, noise, image)
├── data.py           — Data provider fields (priorities, API keys, rate limits)
└── analysis.py       — Analysis fields (factor weights, regime params, backtest)
```

**Rationale**: 5 modules map to the 5 P0 specs we already wrote. Users can navigate to `src/config/llm.py` to find LLM-related config.

### Decision 4: Field assignment by domain
- `llm.py`: litellm_model, llm_channels, llm_temperature, llm_max_tokens, vision_model, agent_litellm_model, openai_api_key, etc.
- `notification.py`: wechat_webhook_url, feishu_webhook_url, telegram_bot_token, telegram_chat_id, email_sender, markdown_to_image_channels, etc. (13 sender fields + routing/noise fields)
- `data.py`: tushare_token, finnhub_api_key, daily_data_priority, realtime_quote_priority, efinance_enabled, websocket_realtime_enabled, etc.
- `analysis.py`: stock_list, factor_weights, regime_thresholds, backtest_eval_window_days, agent_skills, agent_arch, trading_day_check_enabled, etc.
- `base.py`: Everything else (schedule, webui, auth, portfolio, platform, paths, etc.)

## Risks / Trade-offs

[Risk] 99 importers use `from src.config import Config, get_config` → if `__init__.py` doesn't re-export correctly, everything breaks
→ Mitigation: `__init__.py` re-exports exactly the same symbols. Add integration test that imports Config and verifies all 200+ fields.

[Risk] Sub-module dataclass field ordering must match current — dataclass init relies on declaration order
→ Mitigation: Extract fields in declaration order from current class. Verify via `dataclasses.fields(Config)` comparison.

[Risk] 35+ config tests expect specific import paths for mocking
→ Mitigation: Tests import from `src.config` (which re-exports), no test changes needed.

## Migration Plan

1. Create `src/config/` directory with `__init__.py` re-exporting everything
2. Extract domain fields into sub-modules one at a time, running the config test suite after each
3. Once all fields are extracted, delete the monolithic `src/config.py` (keeping only `setup_env()` which belongs in `base.py`)
4. Run full CI gate (`./scripts/ci_gate.sh`), backtest dry-run, and notification diagnostics
5. Rollback: revert to monolithic config.py (simple git revert)

## Open Questions

- Should `config_registry.py` (2998 lines) also be split? → Defer to follow-up
- Should the 750-line constructor be split per-module? → Defer to follow-up
- Do we need a `ConfigBase` ABC? → No — Python dataclass inheritance is sufficient
