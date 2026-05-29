"""
===================================
数据源策略层 - 包初始化
===================================

本包实现策略模式管理多个数据源，实现：
1. 统一的数据获取接口
2. 自动故障切换
3. 防封禁流控策略

数据源优先级（动态调整）：
【配置了 TUSHARE_TOKEN 时】
1. TushareFetcher (Priority 0) - 🔥 最高优先级（动态提升）
2. EfinanceFetcher (Priority 0) - 同优先级
3. AkshareFetcher (Priority 1) - 来自 akshare 库
4. PytdxFetcher (Priority 2) - 来自 pytdx 库（通达信）
5. BaostockFetcher (Priority 3) - 来自 baostock 库
6. YfinanceFetcher (Priority 4) - 来自 yfinance 库

【未配置 TUSHARE_TOKEN 时】
1. EfinanceFetcher (Priority 0) - 最高优先级，来自 efinance 库
2. AkshareFetcher (Priority 1) - 来自 akshare 库
3. PytdxFetcher (Priority 2) - 来自 pytdx 库（通达信）
4. TushareFetcher (Priority 2) - 来自 tushare 库（不可用）
5. BaostockFetcher (Priority 3) - 来自 baostock 库
6. YfinanceFetcher (Priority 4) - 来自 yfinance 库
7. LongbridgeFetcher (Priority 5) - 长桥 OpenAPI（美股/港股兜底）

提示：优先级数字越小越优先，同优先级按初始化顺序排列
"""

from .akshare_fetcher import AkshareFetcher, is_hk_stock_code
from .alphavantage_fetcher import AlphaVantageFetcher
from .baostock_fetcher import BaostockFetcher
from .base import BaseFetcher, DataFetcherManager
from .efinance_fetcher import EfinanceFetcher
from .finnhub_fetcher import FinnhubFetcher
from .longbridge_fetcher import LongbridgeFetcher
from .pytdx_fetcher import PytdxFetcher
from .tushare_fetcher import TushareFetcher
from .us_index_mapping import US_INDEX_MAPPING, get_us_index_yf_symbol, is_us_index_code, is_us_stock_code
from .websocket_realtime_integration import create_websocket_aware_fetcher_manager, is_websocket_enabled
from .yfinance_fetcher import YfinanceFetcher

__all__ = [
    "BaseFetcher",
    "DataFetcherManager",
    "EfinanceFetcher",
    "AkshareFetcher",
    "TushareFetcher",
    "PytdxFetcher",
    "BaostockFetcher",
    "YfinanceFetcher",
    "LongbridgeFetcher",
    "FinnhubFetcher",
    "AlphaVantageFetcher",
    "is_us_index_code",
    "is_us_stock_code",
    "is_hk_stock_code",
    "get_us_index_yf_symbol",
    "US_INDEX_MAPPING",
    "create_websocket_aware_fetcher_manager",
    "is_websocket_enabled",
]


def create_fetcher_manager(*args, **kwargs) -> DataFetcherManager:
    """创建 DataFetcherManager，WebSocket 启用时自动注入 WS 优先逻辑

    Args:
        *args: 传递给 DataFetcherManager 的位置参数
        **kwargs: 传递给 DataFetcherManager 的关键字参数

    Returns:
        DataFetcherManager 实例（WebSocket 启用时为包装后的 WS 感知版本）
    """
    manager = DataFetcherManager(*args, **kwargs)
    if is_websocket_enabled():
        manager = create_websocket_aware_fetcher_manager(manager)
    return manager
