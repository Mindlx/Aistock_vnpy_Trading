## 1. Setup module structure

- [x] 1.1 Create `src/config_llm.py` with LLMConfig dataclass
- [x] 1.2 Create `src/config_notification.py` with NotificationConfig dataclass
- [x] 1.3 Create `src/config_data.py` with DataConfig dataclass
- [x] 1.4 Create `src/config_analysis.py` with AnalysisConfig dataclass
- [x] 1.5 Make `Config` inherit from all four: `class Config(LLMConfig, NotificationConfig, DataConfig, AnalysisConfig)`
- [x] 1.6 Verify: py_compile x5, GitNexus LOW risk, 0 affected processes

## 2. Extract LLM fields (53 fields migrated)

- [x] 2.1 Move 53 LLM field declarations from Config to LLMConfig dataclass
- [x] 2.2 Move `AGENT_MAX_STEPS_DEFAULT` constant to config_llm.py
- [x] 2.3 Verify: LLMConfig.fields length = 53, py_compile passes

## 3. Extract Notification fields (52 fields migrated)

- [x] 3.1 Move 52 notification field declarations from Config to NotificationConfig dataclass
- [x] 3.2 Verify: NotificationConfig.fields length = 52, py_compile passes

## 4. Extract Data Provider fields (37 fields migrated)

- [x] 4.1 Move 37 data provider field declarations from Config to DataConfig dataclass
- [x] 4.2 Move `FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT` constant to config_data.py
- [x] 4.3 Verify: DataConfig.fields length = 37, py_compile passes

## 5. Extract Analysis fields (39 fields migrated)

- [x] 5.1 Move 39 analysis field declarations from Config to AnalysisConfig dataclass
- [x] 5.2 Verify: AnalysisConfig.fields length = 39, py_compile passes

## 6. Finalize and verify

- [x] 6.1 Verify `from src.config import Config, get_config` works
- [x] 6.2 All 5 files pass `python -m py_compile`
- [x] 6.3 `openspec validate` passes for all specs and this change
- [x] 6.4 GitNexus: LOW risk, 0 affected processes
- [x] 6.5 Update AGENTS.md Change Safety Checklist

## Notes

- **181/224 fields migrated** across 4 domain sub-modules
- **43 infrastructure fields** remain in config.py (schedule, webui, auth, bot)
- **_load_from_env() constructor refactoring deferred** to a follow-up change
  (770-line method with cross-domain references requires dedicated testing)
- **Zero breaking changes**: all 99 importers use `from src.config import Config`
