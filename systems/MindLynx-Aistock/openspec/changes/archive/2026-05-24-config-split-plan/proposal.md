## Why

`src/config.py:Config` is a 2802-line monolithic dataclass with 99 direct importers spanning every module. Any field addition/rename touches virtually every file. The class has grown organically and lacks internal structure — LLM config fields sit next to notification fields, data provider settings next to portfolio parameters. The recent audit identified thread-safety gaps and validate-isolation issues that are harder to fix in a monolith. Splitting Config into domain-focused sub-modules would reduce blast radius per change, improve code navigation, and enable per-domain validation.

## What Changes

- Create `src/config/` package with sub-modules: `base.py` (singleton, hot-reload, validate), `llm.py` (LLM channels, models, temperature), `notification.py` (channel configs, routing, noise), `data.py` (provider priorities, API keys, rate limits), `analysis.py` (factor weights, regime params, backtest settings)
- `Config` class becomes a composed dataclass importing fields from each sub-module via mixin/dataclass inheritance
- `get_config()` returns the same `Config` type — **zero breaking changes for 99 importers**
- Thread-safety and auto-validate improvements from the audit are implemented as part of the refactor
- `config_registry.py` split updated to match the new structure

## Capabilities

### New Capabilities
- `config-modularization`: Domain-specific sub-config modules (llm, notification, data, analysis) with clear ownership boundaries

### Modified Capabilities
- `config`: Singleton pattern evolves from monolithic dataclass to composed dataclass. Hot-reload and validate behavior SHALL remain identical. Thread-safety lock added. Auto-validate at init added.

## Impact

- **Code**: `src/config.py` (2802 lines) split into `src/config/base.py` + `src/config/llm.py` + `src/config/notification.py` + `src/config/data.py` + `src/config/analysis.py`. 99 importers require zero changes if interface preserved.
- **Tests**: `tests/test_config_*.py` (35+ test methods) must still pass. New tests for per-domain validation.
- **Documentation**: `AGENTS.md` Change Safety Checklist updated with new file paths. `openspec/specs/config/spec.md` delta spec needed.
- **Risk**: HIGH — Config is the most central module. Mitigated by preserving identical public API and comprehensive test coverage.
