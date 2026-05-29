"""
=============================================
WebSocket Realtime Provider - DataFetcherManager 集成层
=============================================

提供与 DataFetcherManager 的 WebSocket 集成，不修改现有文件。

使用方式：
    from data_provider.websocket_realtime_integration import create_websocket_aware_manager

    manager = create_websocket_aware_manager()
    # 当 WEBSOCKET_REALTIME_ENABLED=true 且代码为 A 股时，
    # 自动使用 WebSocket 获取实时行情
    quote = manager.get_realtime_quote("600519")
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def is_websocket_enabled() -> bool:
    """
    检查是否启用了 WebSocket 实时行情

    优先检查 WEBSOCKET_REALTIME_ENABLED 环境变量（可在运行时临时覆盖），
    其次从 Config 单例读取。

    Returns:
        True 表示启用 WebSocket 模式
    """
    import os

    env_val = os.getenv("WEBSOCKET_REALTIME_ENABLED")
    if env_val is not None:
        return env_val.strip().lower() in ("true", "1", "yes")
    try:
        from src.config import get_config

        cfg = get_config()
        if hasattr(cfg, "websocket_realtime_enabled"):
            return cfg.websocket_realtime_enabled
    except (ImportError, Exception):
        pass
    return False


def get_websocket_reconnect_delay() -> int:
    """
    获取 WebSocket 重连延迟配置

    Returns:
        重连延迟（秒）
    """
    import os

    try:
        return max(1, int(os.getenv("WEBSOCKET_RECONNECT_DELAY", "5").strip()))
    except (ValueError, TypeError):
        return 5


async def get_realtime_quote_websocket_first(
    stock_code: str,
    http_fallback: Callable[..., Any],
    *,
    log_final_failure: bool = True,
) -> Any:
    """
    优先使用 WebSocket 获取实时行情，失败时降级到 HTTP

    这是一个集成辅助函数，可以在外部包装 DataFetcherManager.get_realtime_quote()。
    当 WEBSOCKET_REALTIME_ENABLED=true 时，先尝试 WebSocket 获取，
    失败后自动降级到传入的 http_fallback 回调。

    Args:
        stock_code: 股票代码
        http_fallback: HTTP 降级回调函数，签名同 DataFetcherManager.get_realtime_quote
        log_final_failure: 是否在最终失败时记录日志

    Returns:
        UnifiedRealtimeQuote 对象，或 None
    """
    from data_provider.websocket_realtime import get_quotes_via_websocket

    # A 股代码（纯 6 位数字）才走 WebSocket
    normalized = stock_code.strip()
    if normalized.startswith(("HK", "hk")):
        # 港股走 HTTP
        return http_fallback(stock_code, log_final_failure=log_final_failure)

    # 检查是否纯数字（A 股）
    is_a_share = normalized.isdigit() and len(normalized) == 6
    if not is_a_share:
        # 美股/其他走 HTTP
        return http_fallback(stock_code, log_final_failure=log_final_failure)

    try:
        reconnect_delay = get_websocket_reconnect_delay()
        quotes = await get_quotes_via_websocket(
            codes=[normalized],
            reconnect_delay=reconnect_delay,
            max_retries=2,
        )
        if quotes:
            return quotes[0]

        logger.info("[WebSocket集成] WebSocket 未能获取 %s 行情，降级到 HTTP", normalized)
    except Exception as exc:
        logger.warning("[WebSocket集成] WebSocket 获取 %s 失败: %s，降级到 HTTP", normalized, exc)

    # 降级到 HTTP
    return http_fallback(stock_code, log_final_failure=log_final_failure)


def create_websocket_aware_fetcher_manager(manager: Any | None = None) -> Any:
    """
    创建 DataFetcherManager 的 WebSocket 感知代理

    如果传入已有的 manager 实例，直接包装之；否则创建新的 DataFetcherManager。

    返回一个代理对象，包装 DataFetcherManager，其 get_realtime_quote 方法
    在 WEBSOCKET_REALTIME_ENABLED=true 时优先使用 WebSocket。

    Args:
        manager: 现有的 DataFetcherManager 实例，为 None 时自动创建

    Returns:
        包装后的 DataFetcherManager 实例（如果 WebSocket 未启用则返回普通实例）
    """
    from data_provider.base import DataFetcherManager

    if manager is None:
        manager = DataFetcherManager()

    if not is_websocket_enabled():
        logger.info("[WebSocket集成] WebSocket 实时行情未启用 (WEBSOCKET_REALTIME_ENABLED=false)")
        return manager

    logger.info("[WebSocket集成] WebSocket 实时行情已启用")

    # 保存原始方法
    original_get_realtime = manager.get_realtime_quote

    # 创建同步兼容的包装方法
    def _ws_aware_get_realtime(stock_code: str, *, log_final_failure: bool = True):
        """
        WebSocket 优先的 get_realtime_quote

        同步接口，内部通过 asyncio.run() 运行异步 WebSocket 操作。
        适用于 FastAPI/server 环境已有事件循环的情况。
        """
        if not is_websocket_enabled():
            return original_get_realtime(stock_code, log_final_failure=log_final_failure)

        # A 股代码
        normalized = stock_code.strip()
        is_a_share = normalized.isdigit() and len(normalized) == 6

        if not is_a_share:
            return original_get_realtime(stock_code, log_final_failure=log_final_failure)

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # 已有事件循环运行中，直接使用
                future = asyncio.run_coroutine_threadsafe(
                    get_realtime_quote_websocket_first(
                        stock_code,
                        lambda sc, **kw: original_get_realtime(sc, **kw),
                        log_final_failure=log_final_failure,
                    ),
                    loop,
                )
                return future.result(timeout=15)
        except RuntimeError:
            # 没有运行中的事件循环，创建新的
            pass

        # 同步上下文：创建新的事件循环
        try:
            return asyncio.run(
                get_realtime_quote_websocket_first(
                    stock_code,
                    lambda sc, **kw: original_get_realtime(sc, **kw),
                    log_final_failure=log_final_failure,
                )
            )
        except Exception as exc:
            logger.warning("[WebSocket集成] async 执行失败: %s，降级到 HTTP", exc)
            return original_get_realtime(stock_code, log_final_failure=log_final_failure)

    # 替换方法
    manager.get_realtime_quote = _ws_aware_get_realtime  # type: ignore[method-assign]
    return manager
