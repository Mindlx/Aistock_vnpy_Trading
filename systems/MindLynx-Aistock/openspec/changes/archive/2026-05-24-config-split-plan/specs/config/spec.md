## MODIFIED Requirements

### Requirement: Configuration SHALL be accessible as a singleton
The system SHALL provide a single `Config` dataclass instance accessible via `get_config()` or `Config.get_instance()`. The singleton SHALL be thread-safe using `threading.Lock`. All system modules SHALL read their configuration from this singleton rather than direct `os.environ` access. The Config class SHALL be organized into domain-specific sub-modules under `src/config/` (llm, notification, data, analysis) while preserving backward-compatible access via `from src.config import Config, get_config`.

#### Scenario: Config singleton access
- **WHEN** any module calls `get_config()`
- **THEN** it SHALL receive the same initialized singleton instance, regardless of which sub-module the Config class inherits from

#### Scenario: Thread-safe singleton creation
- **WHEN** two threads simultaneously call `get_instance()` before the singleton exists
- **THEN** only one Config instance SHALL be created due to `threading.Lock` protection

### Requirement: Configuration SHALL be loaded from environment variables
The system SHALL read all configuration values from `os.environ`, with domain-specific fields organized in sub-modules. Fields SHALL be attributed to sub-modules as follows: LLM fields (channels, models, temperature) in `src/config/llm.py`, notification fields (channel URLs, routing, noise) in `src/config/notification.py`, data provider fields (priorities, API keys) in `src/config/data.py`, analysis fields (factor weights, regime params) in `src/config/analysis.py`, and infrastructure fields (schedule, auth, webui) in `src/config/base.py`.

#### Scenario: Backward-compatible import
- **WHEN** any existing module writes `from src.config import Config, get_config`
- **THEN** it SHALL work identically — `__init__.py` SHALL re-export the same symbols

## ADDED Requirements

### Requirement: Config SHALL validate at initialization
The config singleton SHALL automatically run `validate_structured()` after construction. Validation issues SHALL be logged as warnings (never blocking startup). Errors SHALL be logged at ERROR level. This replaces the previous behavior where validation was only triggered manually.

#### Scenario: Config validation on init
- **WHEN** `Config.get_instance()` creates a new singleton via `_load_from_env()`
- **THEN** it SHALL call `self.validate_structured()` and log any issues found, without blocking the creation flow
