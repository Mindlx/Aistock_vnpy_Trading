"""Aistock_vnpy_Trading 数据仓库服务层

统一的数据缓存 + 限流 + 调度层, 为三个子系统提供一致的数据访问。

公开 API:
    WarehouseReader  — 统一读取接口 (缓存优先 → 降级 API)
    TokenBucketLimiter — 跨进程令牌桶限流器
    DataWarmer       — 首次部署预热脚本
    RefreshScheduler — 数据刷新调度器
    DataWarehouseConfig — 全局配置

用法:
    from services.data_warehouse import WarehouseReader
    reader = WarehouseReader()
    df = reader.get_daily("600519", days=120)
"""
from __future__ import annotations

from services.data_warehouse.config import DataWarehouseConfig, RateLimitConfig, DataTypeConfig
from services.data_warehouse.limiter import TokenBucketLimiter, RateLimitError
from services.data_warehouse.storage import DataLake
from services.data_warehouse.warehouse import WarehouseReader
from services.data_warehouse.scheduler import RefreshScheduler, run_scheduler_daemon
from services.data_warehouse.warmer import DataWarmer

__all__ = [
    "WarehouseReader",
    "TokenBucketLimiter",
    "DataWarmer",
    "RefreshScheduler",
    "DataLake",
    "DataWarehouseConfig",
    "RateLimitConfig",
    "DataTypeConfig",
    "RateLimitError",
    "run_scheduler_daemon",
]
