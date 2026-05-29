from dataclasses import dataclass, field

FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT = 8.0


@dataclass
class DataConfig:
    """Data provider configuration fields extracted from Config dataclass."""
    tushare_token: str | None = None
    tickflow_api_key: str | None = None
    finnhub_api_key: str | None = None
    alphavantage_api_key: str | None = None
    longbridge_app_key: str | None = None
    longbridge_app_secret: str | None = None
    longbridge_access_token: str | None = None
    tavily_api_keys: list[str] = field(default_factory=list)  # Tavily API Keys
    brave_api_keys: list[str] = field(default_factory=list)  # Brave Search API Keys
    serpapi_keys: list[str] = field(default_factory=list)  # SerpAPI Keys
    searxng_base_urls: list[str] = field(default_factory=list)  # SearXNG instance URLs (self-hosted, no quota)
    searxng_public_instances_enabled: bool = True  # Auto-discover public SearXNG instances when base URLs are absent
    social_sentiment_api_key: str | None = None
    social_sentiment_api_url: str = "https://api.adanos.org"
    news_max_age_days: int = 3  # 新闻最大时效（天）
    news_strategy_profile: str = "short"  # 新闻窗口策略档位：ultra_short/short/medium/long
    enable_realtime_quote: bool = True
    enable_realtime_technical_indicators: bool = True
    enable_chip_distribution: bool = True
    enable_eastmoney_patch: bool = False
    realtime_source_priority: str = "efinance,tencent,akshare_sina,akshare_em"
    realtime_cache_ttl: int = 600
    circuit_breaker_cooldown: int = 300
    websocket_realtime_enabled: bool = False  # 默认关闭，稳定后开放
    websocket_reconnect_delay: int = 5  # 重连延迟（秒）
    enable_fundamental_pipeline: bool = True
    fundamental_stage_timeout_seconds: float = FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT
    fundamental_fetch_timeout_seconds: float = 3.0
    fundamental_retry_max: int = 1
    fundamental_cache_ttl_seconds: int = 120
    fundamental_cache_max_entries: int = 256
    akshare_sleep_min: float = 2.0
    akshare_sleep_max: float = 5.0
    tushare_rate_limit_per_minute: int = 80
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0
