"""
===================================
交易日历模块 (Issue #373)
===================================

职责：
1. 按市场（A股/港股/美股）判断当日是否为交易日
2. 按市场时区取“今日”日期，避免服务器 UTC 导致日期错误
3. 支持 per-stock 过滤：只分析当日开市市场的股票

依赖：exchange-calendars（可选，不可用时 fail-open）
"""

import logging
from datetime import date, datetime, time as dtime
from enum import Enum
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Exchange-calendars availability
_XCALS_AVAILABLE = False
try:
    import exchange_calendars as xcals

    _XCALS_AVAILABLE = True
except ImportError:
    logger.warning("exchange-calendars not installed; trading day check disabled. Run: pip install exchange-calendars")

# Market -> exchange code (exchange-calendars)
MARKET_EXCHANGE = {"cn": "XSHG", "hk": "XHKG", "us": "XNYS", "jp": "XTKS", "kr": "XKRX"}

# Market -> IANA timezone for "today"
MARKET_TIMEZONE = {
    "cn": "Asia/Shanghai",
    "hk": "Asia/Hong_Kong",
    "us": "America/New_York",
    "jp": "Asia/Tokyo",
    "kr": "Asia/Seoul",
}


class MarketPhase(str, Enum):
    """Market phase labels for compact status display."""

    PREMARKET = "premarket"
    INTRADAY = "intraday"
    LUNCH_BREAK = "lunch_break"
    CLOSING_AUCTION = "closing_auction"
    POSTMARKET = "postmarket"
    NON_TRADING = "non_trading"
    UNKNOWN = "unknown"


# Market session boundaries (local time, inclusive-exclusive for intraday)
_MARKET_SESSIONS: dict[str, tuple[tuple[dtime, dtime], ...]] = {
    "cn": (
        (dtime(9, 30), dtime(11, 30)),    # morning session
        (dtime(13, 0), dtime(14, 57)),      # afternoon session
    ),
    "hk": (
        (dtime(9, 30), dtime(15, 55)),
    ),
    "us": (
        (dtime(9, 30), dtime(15, 55)),
    ),
    "jp": (
        (dtime(9, 0), dtime(11, 30)),     # morning session
        (dtime(12, 30), dtime(14, 55)),    # afternoon session
    ),
    "kr": (
        (dtime(9, 0), dtime(15, 25)),
    ),
}
# Closing auction window (minutes before main session end)
_CLOSING_AUCTION_WINDOW_MINUTES: dict[str, int] = {
    "cn": 3,   # 14:57-15:00
    "hk": 5,   # 15:55-16:00
    "us": 5,   # 15:55-16:00
    "jp": 5,   # 14:55-15:00
    "kr": 5,   # 15:25-15:30
}
# Lunch break (A-share morning 11:30-13:00, JP 11:30-12:30)
_LUNCH_BREAK = (dtime(11, 30), dtime(13, 0))


def infer_market_phase(
    market: str | None,
    current_time: datetime | None = None,
) -> MarketPhase:
    """Infer the current market phase for a given market."""
    if not market or market not in MARKET_TIMEZONE:
        return MarketPhase.UNKNOWN
    market_now = get_market_now(market, current_time=current_time)
    # Check if it's a non-trading day
    if not is_market_open(market, market_now.date()):
        return MarketPhase.NON_TRADING
    now_time = market_now.time()

    sessions = _MARKET_SESSIONS.get(market)
    if not sessions:
        return MarketPhase.UNKNOWN

    # Check lunch break (A-shares only)
    if market == "cn" and _LUNCH_BREAK[0] <= now_time < _LUNCH_BREAK[1]:
        return MarketPhase.LUNCH_BREAK

    # Check closing auction window
    for open_t, close_t in sessions:
        close_h, close_m = close_t.hour, close_t.minute
        auction_start = dtime(close_h, close_m)
        auction_end = dtime(
            close_h + (close_m + _CLOSING_AUCTION_WINDOW_MINUTES.get(market, 3)) // 60,
            (close_m + _CLOSING_AUCTION_WINDOW_MINUTES.get(market, 3)) % 60,
        )
        if auction_start <= now_time < auction_end:
            return MarketPhase.CLOSING_AUCTION

    # Check intraday (market is open)
    for open_t, close_t in sessions:
        if open_t <= now_time < close_t:
            return MarketPhase.INTRADAY

    # Premarket: before first session open
    first_open = sessions[0][0]
    if now_time < first_open:
        return MarketPhase.PREMARKET

    # Postmarket: after last session close
    last_close = sessions[-1][1]
    if now_time >= last_close:
        return MarketPhase.POSTMARKET

    return MarketPhase.UNKNOWN


def get_market_for_stock(code: str) -> str | None:
    """
    Infer market region for a stock code.

    Returns:
        'cn' | 'hk' | 'us' | 'jp' | 'kr' | None (None = unrecognized, fail-open: treat as open)
    """
    if not code or not isinstance(code, str):
        return None
    code = (code or "").strip().upper()

    from data_provider import is_hk_stock_code, is_jp_stock_code, is_kr_stock_code, is_us_index_code, is_us_stock_code

    if is_us_stock_code(code) or is_us_index_code(code):
        return "us"
    if is_hk_stock_code(code):
        return "hk"
    if is_jp_stock_code(code):
        return "jp"
    if is_kr_stock_code(code):
        return "kr"
    # A-share: 6-digit numeric
    if code.isdigit() and len(code) == 6:
        return "cn"
    return None


def is_market_open(market: str, check_date: date) -> bool:
    """
    Check if the given market is open on the given date.

    Fail-open: returns True if exchange-calendars unavailable or date out of range.

    Args:
        market: 'cn' | 'hk' | 'us'
        check_date: Date to check

    Returns:
        True if trading day (or fail-open), False otherwise
    """
    if not _XCALS_AVAILABLE:
        return True
    ex = MARKET_EXCHANGE.get(market)
    if not ex:
        return True
    try:
        cal = xcals.get_calendar(ex)
        session = datetime(check_date.year, check_date.month, check_date.day)
        return cal.is_session(session)
    except Exception as e:
        logger.warning("trading_calendar.is_market_open fail-open: %s", e)
        return True


def get_market_now(market: str | None, current_time: datetime | None = None) -> datetime:
    """
    Return current time in the market's local timezone.

    If current_time is naive, treat it as already expressed in the market timezone.
    Unknown markets fall back to the given datetime (or local system time).
    """
    tz_name = MARKET_TIMEZONE.get(market or "")

    if current_time is None:
        if tz_name:
            return datetime.now(ZoneInfo(tz_name))
        return datetime.now()

    if not tz_name:
        return current_time

    tz = ZoneInfo(tz_name)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=tz)
    return current_time.astimezone(tz)


def get_effective_trading_date(market: str | None, current_time: datetime | None = None) -> date:
    """
    Resolve the latest reusable daily-bar date for checkpoint/resume logic.

    Rules:
    - Non-trading day / holiday: previous trading session
    - Trading day before market close: previous completed trading session
    - Trading day after market close: current trading session
    - Calendar lookup failure: fail-open to market-local natural date
    """
    market_now = get_market_now(market, current_time=current_time)
    fallback_date = market_now.date()

    if not _XCALS_AVAILABLE:
        return fallback_date

    ex = MARKET_EXCHANGE.get(market or "")
    tz_name = MARKET_TIMEZONE.get(market or "")
    if not ex or not tz_name:
        return fallback_date

    try:
        cal = xcals.get_calendar(ex)
        local_date = market_now.date()

        if not cal.is_session(local_date):
            return cal.date_to_session(local_date, direction="previous").date()

        session = cal.date_to_session(local_date, direction="previous")
        session_close = cal.session_close(session)
        if hasattr(session_close, "tz_convert"):
            close_local = session_close.tz_convert(tz_name).to_pydatetime()
        elif session_close.tzinfo is not None:
            close_local = session_close.astimezone(ZoneInfo(tz_name))
        else:
            close_local = session_close.replace(tzinfo=ZoneInfo(tz_name))

        if market_now >= close_local:
            return session.date()

        return cal.previous_session(session).date()
    except Exception as e:
        logger.warning("trading_calendar.get_effective_trading_date fail-open: %s", e)
        return fallback_date


def get_open_markets_today() -> set[str]:
    """
    Get markets that are open today (by each market's local timezone).

    Returns:
        Set of market keys ('cn', 'hk', 'us') that are trading today
    """
    if not _XCALS_AVAILABLE:
        return {"cn", "hk", "us"}
    result: set[str] = set()
    for mkt, tz_name in MARKET_TIMEZONE.items():
        try:
            tz = ZoneInfo(tz_name)
            today = datetime.now(tz).date()
            if is_market_open(mkt, today):
                result.add(mkt)
        except Exception as e:
            logger.warning("get_open_markets_today fail-open for %s: %s", mkt, e)
            result.add(mkt)
    return result


def compute_effective_region(config_region: str, open_markets: set[str]) -> str | None:
    """
    Compute effective market review region given config and open markets.

    Args:
        config_region: From MARKET_REVIEW_REGION ('cn' | 'hk' | 'us' | 'both')
        open_markets: Markets open today

    Returns:
        None: caller uses config default (check disabled)
        '': all relevant markets closed, skip market review
        'cn' | 'hk' | 'us' | 'both': effective subset for today
    """
    if config_region not in ("cn", "hk", "us", "both"):
        config_region = "cn"
    if config_region in ("cn", "hk", "us"):
        return config_region if config_region in open_markets else ""
    # both: return only the markets that are actually open today
    parts = [m for m in ("cn", "hk", "us") if m in open_markets]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ",".join(parts)
