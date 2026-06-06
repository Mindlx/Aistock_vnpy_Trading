"""雪球 (Xueqiu) sentiment fetcher — Chinese equivalent of StockTwits.

雪球 is China's largest retail investor social platform, comparable to
StockTwits for the US market. Users share investment ideas, debate stocks,
and tag sentiment. This fetcher retrieves hot discussions for A-share tickers
and formats them for prompt injection into the Sentiment Analyst.

Degrades gracefully — returns a placeholder rather than raising.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Max 3-digit timestamp padding
_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _bare_code(ticker: str) -> str:
    """Extract bare 6-digit A-share code from ticker."""
    return ticker.replace(".SS", "").replace(".SZ", "").replace(".SH", "").strip()


def fetch_xueqiu_hot_tweets(ticker: str, limit: int = 20) -> str:
    """Fetch market sentiment data via EastMoney comment analysis.

    Uses per-stock comment detail APIs from EastMoney as a proxy for
    retail investor sentiment (replaces StockTwits for A-shares).

    Args:
        ticker: A-share symbol (e.g., 601801.SS, 000001.SZ)
        limit: Max items to return

    Returns:
        Formatted text block for prompt injection, or placeholder.
    """
    code = _bare_code(ticker)
    try:
        import akshare as ak

        # Stock rating commentary from EastMoney (per-stock)
        df = ak.stock_comment_detail_scrd_desire_em(symbol=code)
        if df is None or df.empty:
            return _placeholder(f"东方财富: No rating data for {code}")

        lines = [
            f"## 东方财富个股评级 — {code}",
            f"来源: 东方财富 (data.eastmoney.com) · {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]
        for _, row in df.head(limit).iterrows():
            item = " | ".join(f"{k}: {v}" for k, v in row.items() if v)
            if item:
                lines.append(f"- {item}")
        return "\n".join(lines) if len(lines) > 2 else _placeholder(f"东方财富: No data for {code}")

    except ImportError:
        logger.warning("akshare not installed")
        return _placeholder("东方财富: Library not available")
    except Exception as e:
        logger.warning(f"EastMoney rating fetch failed for {ticker}: {e}")
        return _placeholder(f"东方财富: Fetch failed ({e})")


def fetch_xueqiu_stock_comments(ticker: str, limit: int = 30) -> str:
    """Fetch investor focus/sentiment data — replaces Reddit for A-shares.

    Uses EastMoney per-stock investor focus analysis API to gauge
    retail investor attention and sentiment.

    Args:
        ticker: A-share symbol
        limit: Max items to return

    Returns:
        Formatted text block with sentiment-like data.
    """
    code = _bare_code(ticker)
    try:
        import akshare as ak

        # Investor focus analysis (per-stock)
        df = ak.stock_comment_detail_scrd_focus_em(symbol=code)
        if df is None or df.empty:
            return _placeholder(f"东方财富: No focus data for {code}")

        lines = [
            f"## 东方财富投资者关注度 — {code}",
            f"来源: 东方财富 · {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]
        for _, row in df.head(limit).iterrows():
            parts = [f"{k}: {v}" for k, v in row.items() if v]
            if parts:
                lines.append(f"- {' | '.join(parts)}")
        return "\n".join(lines) if len(lines) > 2 else _placeholder(f"东方财富: No focus data for {code}")

    except ImportError:
        return _placeholder("东方财富: Library not available")
    except Exception as e:
        logger.warning(f"EastMoney focus fetch failed for {ticker}: {e}")
        return _placeholder(f"东方财富: Fetch failed ({e})")


def _placeholder(reason: str) -> str:
    """Return a placeholder block when data is unavailable."""
    return (
        f"<unavailable>\n"
        f"Chinese sentiment data unavailable for this ticker.\n"
        f"Reason: {reason}\n"
        f"</unavailable>"
    )


def fetch_desire_raw(symbol: str):
    """返回东方财富参与意愿原始DataFrame（最新一条）。"""
    from akshare import stock_comment_detail_scrd_desire_em as _api
    df = _api(symbol=symbol)
    if df is not None and not df.empty:
        df = df.sort_values("交易日期")
    return df


def fetch_focus_raw(symbol: str):
    """返回东方财富用户关注指数原始DataFrame（最新一条）。"""
    from akshare import stock_comment_detail_scrd_focus_em as _api
    df = _api(symbol=symbol)
    if df is not None and not df.empty:
        df = df.sort_values("交易日")
    return df
