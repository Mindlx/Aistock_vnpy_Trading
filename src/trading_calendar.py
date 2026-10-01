"""交易日历工具 — 判断某个市场在某日是否开市。

用于给定时任务加"非交易日跳过"门禁，避免节假日仍执行全量分析（LLM 重活）。

设计要点：
- 复用 exchange-calendars（交易所官方日历，与 MindLynx-Aistock 一致），
  比"周一~周五"更准确（法定节假日/调休均能识别）。
- Fail-open：日历库缺失、市场未知或日期非法时返回 True（视为交易日），
  宁可多跑一次也不误伤真实交易日。
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import exchange_calendars as _xcals

    _XCALS_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖缺失时走 fail-open
    _xcals = None
    _XCALS_AVAILABLE = False

MARKET_EXCHANGE = {
    "cn": "XSHG",   # 上海证券交易所
    "hk": "XHKG",   # 香港交易所
    "us": "XNYS",   # 纽约证券交易所
}


def is_trading_day(date_str: str, market: str = "cn") -> bool:
    """判断 ``date_str``（YYYY-MM-DD，可带时间后缀）是否为 ``market`` 的交易日。

    Fail-open：无法判定时返回 True（不跳过）。
    """
    if not _XCALS_AVAILABLE:
        return True
    exchange = MARKET_EXCHANGE.get(market)
    if not exchange:
        return True
    try:
        check_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("trading_calendar: 无法解析日期 %r，按交易日处理", date_str)
        return True
    try:
        calendar = _xcals.get_calendar(exchange)
        return bool(calendar.is_session(check_date))
    except Exception as exc:  # pragma: no cover - 日历库异常时 fail-open
        logger.warning("trading_calendar.is_trading_day fail-open: %s", exc)
        return True


def should_skip_run(date_str: str, *, force_run: bool = False, market: str = "cn") -> bool:
    """定时任务门禁：非交易日且未强制时返回 True（应跳过）。

    ``force_run=True`` 时始终返回 False，用于手工强制执行。
    """
    if force_run:
        return False
    return not is_trading_day(date_str, market=market)


def should_skip_warmup(
    date_str: str,
    *,
    full_market: bool = False,
    force_run: bool = False,
    market: str = "cn",
) -> bool:
    """数据仓库预热门禁：非交易日且未强制时返回 True（应跳过）。

    例外（不跳过）：
    - ``full_market=True``：全市场历史回填属于一次性/断点续传任务，与当日是否开市无关。
    - ``force_run=True``：手工强制执行。
    """
    if full_market or force_run:
        return False
    return not is_trading_day(date_str, market=market)
