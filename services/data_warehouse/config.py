"""数据仓库配置中心 — TTL矩阵、限流参数、调度计划、股票池
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import ClassVar

DATA_DIR = os.environ.get(
    "DATA_WAREHOUSE_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "data"),
)
DB_PATH = os.path.join(DATA_DIR, "data_warehouse.db")


@dataclass
class RateLimitConfig:
    """每 API 源的令牌桶配置"""
    max_tokens: float     # 桶容量
    refill_rate: float    # 每秒补充速率
    max_retries: int = 3
    base_delay: float = 1.0
    jitter: float = 0.3

    @property
    def per_minute(self) -> float:
        return self.refill_rate * 60


@dataclass
class DataTypeConfig:
    """每数据类型的刷新策略"""
    ttl_seconds: int
    refresh_trigger: str          # cron / interval 描述
    priority: str = "medium"      # critical / high / medium / low
    batch_size: int = 10
    cooldown_between: float = 3.0 # 每只股票间隔(秒)


@dataclass
class DataWarehouseConfig:
    """全局配置——所有可调参数集中在此"""
    # ── 股票池 ──
    stock_pool: list[str] = field(default_factory=lambda: [])

    # ── 数据文件路径 ──
    db_path: str = DB_PATH

    # ── 令牌桶参数 ──
    rate_limits: dict[str, RateLimitConfig] = field(default_factory=lambda: {
        "eastmoney": RateLimitConfig(max_tokens=15, refill_rate=15/60,  base_delay=1.0),
        "sina":      RateLimitConfig(max_tokens=60, refill_rate=60/60,  base_delay=2.0),
        "tencent":   RateLimitConfig(max_tokens=120,refill_rate=120/60, base_delay=0.5),
        "cninfo":    RateLimitConfig(max_tokens=30, refill_rate=30/60,  base_delay=1.0),
        "tushare":   RateLimitConfig(max_tokens=50, refill_rate=50/60,  base_delay=1.0),
        "yfinance":  RateLimitConfig(max_tokens=30, refill_rate=30/60,  base_delay=2.0),
    })

    # ── 数据类型刷新策略 ──
    data_types: dict[str, DataTypeConfig] = field(default_factory=lambda: {
        "daily_ohlcv":          DataTypeConfig(86400,   "cron(15:30, 工作日)",    "high",   10, 3.0),
        "realtime_quotes":     DataTypeConfig(300,     "interval(300s)",         "critical", 10, 0.0),
        "financial_indicators": DataTypeConfig(86400,  "cron(16:00, 工作日)",    "medium",  3,  5.0),
        "capital_flows":        DataTypeConfig(86400,  "cron(16:30, 工作日)",    "low",     5,  3.0),
        "news_events":          DataTypeConfig(3600,   "interval(3600s)",        "medium",  10, 2.0),
        "fundamentals":         DataTypeConfig(604800, "cron(09:00, 周一)",      "low",     5,  5.0),
    })

    # ── SQLite ──
    sqlite_busy_timeout: int = 5000
    sqlite_cache_size: int = -16000  # 16MB

    # ── 运行时 ──
    warm_history_years: int = 1
    read_only: bool = False          # 调试用, 只读模式不写缓存

    # ── 单例 ──
    _instance: ClassVar[DataWarehouseConfig | None] = None

    @classmethod
    def get_instance(cls) -> DataWarehouseConfig:
        if cls._instance is None:
            stock_list: list[str] = []
            # 单源配置：优先从 config/stock_pool.csv 加载
            csv_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "stock_pool.csv")
            if os.path.exists(csv_path):
                with open(csv_path) as f:
                    next(f, None)
                    for line in f:
                        parts = line.strip().split(",")
                        if parts:
                            stock_list.append(parts[0])
            # env STOCK_LIST 可作覆盖（用于临时增减，不修改 CSV）
            try:
                from src.config import get_config
                cfg = get_config()
                env_list = getattr(cfg, "stock_list", [])
                if env_list:
                    stock_list = env_list
            except (ImportError, Exception):
                env_val = os.environ.get("STOCK_LIST", "")
                if env_val:
                    stock_list = [s.strip() for s in env_val.split(",") if s.strip()]
            cls._instance = cls(stock_pool=stock_list)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None
