## Purpose

The Config system provides a centralized, env-var driven configuration singleton that controls all system behavior. Every module reads its settings from this single source of truth. Configuration is loaded from `.env` at startup and can be hot-reloaded during scheduled runs.
## Requirements
### Requirement: Configuration SHALL be loaded from environment variables
The system SHALL read all configuration values from `os.environ`, with domain-specific fields organized in sub-modules. Fields SHALL be attributed to sub-modules as follows: LLM fields (channels, models, temperature) in `src/config_llm.py`, notification fields (channel URLs, routing, noise) in `src/config_notification.py`, data provider fields (priorities, API keys) in `src/config_data.py`, analysis fields (factor weights, regime params) in `src/config_analysis.py`, and infrastructure fields (schedule, auth, webui) remain in `src/config.py`.

#### Scenario: Backward-compatible import
- **WHEN** any existing module writes `from src.config import Config, get_config`
- **THEN** it SHALL work identically — Config inherits from all sub-modules

### Requirement: Configuration SHALL be accessible as a singleton
The system SHALL provide a single `Config` dataclass instance accessible via `get_config()` or `Config.get_instance()`. The singleton SHALL be thread-safe using `threading.Lock`. All system modules SHALL read their configuration from this singleton rather than direct `os.environ` access. The Config class SHALL be organized into domain-specific sub-modules (`src/config_llm.py`, `src/config_notification.py`, `src/config_data.py`, `src/config_analysis.py`) while preserving backward-compatible access via `from src.config import Config, get_config`.

#### Scenario: Config singleton access
- **WHEN** any module calls `get_config()`
- **THEN** it SHALL receive the same initialized singleton instance, regardless of which sub-module the Config class inherits from

#### Scenario: Thread-safe singleton creation
- **WHEN** two threads simultaneously call `get_instance()` before the singleton exists
- **THEN** only one Config instance SHALL be created due to `threading.Lock` protection

### Requirement: The system SHALL validate configuration at startup
On initialization, the system SHALL run `Config.validate()` which checks for missing required values, invalid combinations, and deprecated settings. Validation results SHALL be returned as a list of `ConfigIssue` objects with severity levels. Warnings SHALL NOT block startup.

#### Scenario: Missing optional config
- **WHEN** a non-critical env var is missing (e.g., a notification channel URL)
- **THEN** the system SHALL log a warning and continue, disabling only the affected feature

### Requirement: Config SHALL persist runtime changes via ConfigManager
The system SHALL support runtime configuration persistence through `ConfigManager`. When the WebUI saves settings, `ConfigManager` SHALL write the changes to a persistent config store. On next startup or scheduled reload, these persisted values SHALL be applied.

#### Scenario: WebUI saves a setting
- **WHEN** a user modifies a setting through the WebUI
- **THEN** `ConfigManager` SHALL persist the change and the running Config singleton SHALL reflect the new value

### Requirement: The system SHALL support env-var template documentation
The `.env.example` file SHALL serve as the canonical reference for all configuration variables. It SHALL include variable names, default values, descriptions, and usage notes. When config semantics change, `.env.example` SHALL be updated and impact assessed across local, Docker, GitHub Actions, Web, and Desktop environments.

#### Scenario: Config schema change
- **WHEN** a new env var is added or an existing one changes semantics
- **THEN** `.env.example` SHALL be updated, and the deploy docs SHALL be assessed for compatibility impact

### Requirement: Config SHALL validate at initialization
The config singleton SHALL automatically run `validate_structured()` after construction. Validation issues SHALL be logged as warnings (never blocking startup). Errors SHALL be logged at ERROR level. This replaces the previous behavior where validation was only triggered manually.

#### Scenario: Config validation on init
- **WHEN** `Config.get_instance()` creates a new singleton via `_load_from_env()`
- **THEN** it SHALL call `self.validate_structured()` and log any issues found, without blocking the creation flow

