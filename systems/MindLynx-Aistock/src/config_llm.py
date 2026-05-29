from dataclasses import dataclass, field
from typing import Any, Optional

AGENT_MAX_STEPS_DEFAULT = 10


@dataclass
class LLMConfig:
    """LLM configuration fields extracted from Config dataclass."""
    litellm_model: str = ""  # Primary model; must include provider prefix when set explicitly
    litellm_fallback_models: list[str] = field(default_factory=list)  # Cross-model fallback list
    llm_temperature: float = 0.7
    agent_analysis_temperature: float = 0.3
    litellm_config_path: str | None = None
    llm_models_source: str = "legacy_env"
    llm_channels: list[dict[str, Any]] = field(default_factory=list)
    llm_model_list: list[dict[str, Any]] = field(default_factory=list)
    gemini_api_keys: list[str] = field(default_factory=list)
    anthropic_api_keys: list[str] = field(default_factory=list)
    openai_api_keys: list[str] = field(default_factory=list)
    deepseek_api_keys: list[str] = field(default_factory=list)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-pro-preview"  # 主模型
    gemini_model_fallback: str = "gemini-3-flash-preview"  # 备选模型
    gemini_temperature: float = 0.7  # 温度参数（0.0-2.0，控制输出随机性，默认0.7）
    gemini_request_delay: float = 2.0  # 请求间隔（秒）
    gemini_max_retries: int = 5  # 最大重试次数
    gemini_retry_delay: float = 5.0  # 重试基础延时（秒）
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"  # Claude model name
    anthropic_temperature: float = 0.7  # Anthropic temperature (0.0-1.0, default 0.7)
    anthropic_max_tokens: int = 8192  # Max tokens for Anthropic responses
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # 如: https://api.openai.com/v1
    openai_model: str = "gpt-5.5"  # OpenAI 兼容模型名称
    openai_vision_model: str | None = None  # Deprecated: use VISION_MODEL instead
    openai_temperature: float = 0.7  # OpenAI 温度参数（0.0-2.0，默认0.7）
    vision_model: str = ""
    vision_provider_priority: str = "gemini,anthropic,openai"
    anspire_api_keys: list[str] = field(default_factory=list)  # Anspire Search API Keys
    bocha_api_keys: list[str] = field(default_factory=list)  # Bocha API Keys
    minimax_api_keys: list[str] = field(default_factory=list)  # MiniMax API Keys
    agent_litellm_model: str = ""  # Optional Agent-only primary model; empty inherits LITELLM_MODEL
    agent_mode: bool = False
    agent_max_steps: int = AGENT_MAX_STEPS_DEFAULT
    agent_skills: list[str] = field(default_factory=list)
    agent_skill_dir: str | None = None
    agent_nl_routing: bool = False  # Enable natural language routing in bot dispatcher
    agent_arch: str = "single"  # Agent architecture: 'single' (legacy) or 'multi' (orchestrator)
    agent_orchestrator_mode: str = "standard"  # Orchestrator mode: quick/standard/full/specialist
    agent_orchestrator_timeout_s: int = 600  # Cooperative timeout budget for the whole multi-agent pipeline
    agent_skill_max_concurrent: int = 5  # Max concurrent skill agents in specialist mode
    agent_skill_compact: bool = True  # Use compact 1-liner strategy descriptions (~85% smaller prompt)
    agent_risk_override: bool = True  # Allow risk agent to veto buy signals
    agent_deep_research_budget: int = 30000  # Max token budget for deep research
    agent_deep_research_timeout: int = 180  # Max seconds for /research command before returning timeout
    agent_memory_enabled: bool = False  # Enable memory & calibration system
    agent_skill_autoweight: bool = True  # Auto-weight skills by backtest performance
    agent_skill_routing: str = "auto"  # Skill routing: 'auto' (regime-based) or 'manual'
    agent_event_monitor_enabled: bool = False  # Enable periodic event-driven alert checks in schedule mode
    agent_event_monitor_interval_minutes: int = 5  # Polling interval for event monitor background checks
    agent_event_alert_rules_json: str = ""  # JSON array of serialized EventMonitor rules
