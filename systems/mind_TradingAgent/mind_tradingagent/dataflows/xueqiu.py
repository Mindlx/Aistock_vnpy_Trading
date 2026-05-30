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
    """Fetch hot Xueqiu tweets for an A-share ticker.

    Args:
        ticker: A-share symbol (e.g., 601801.SS, 000001.SZ)
        limit: Max tweets to return

    Returns:
        Formatted text block for prompt injection, or placeholder.
    """
    code = _bare_code(ticker)
    try:
        import akshare as ak

        df = ak.stock_hot_tweet_xq(symbol=code)
        if df is None or df.empty:
            return _placeholder(f"雪球: No hot tweets found for {code}")

        lines = [
            f"## 雪球热门讨论 — {code}",
            f"来源: 雪球 (xueqiu.com) · {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]
        for _, row in df.head(limit).iterrows():
            title = row.get("标题", row.get("title", ""))
            text = row.get("内容", row.get("text", ""))
            user = row.get("用户名", row.get("user", ""))
            retweet = row.get("转发数", row.get("retweet_count", 0))
            like = row.get("点赞数", row.get("like_count", 0))
            if title or text:
                lines.append(f"**{title or text[:80]}**")
                lines.append(f"  作者: {user} | 转发: {retweet} 点赞: {like}")
                lines.append("")
        return "\n".join(lines) if len(lines) > 2 else _placeholder(f"雪球: No data for {code}")

    except ImportError:
        logger.warning("akshare not installed, cannot fetch Xueqiu data")
        return _placeholder("雪球: Library not available")
    except Exception as e:
        logger.warning(f"Xueqiu fetch failed for {ticker}: {e}")
        return _placeholder(f"雪球: Fetch failed ({e})")


def fetch_xueqiu_stock_comments(ticker: str, limit: int = 30) -> str:
    """Fetch Xueqiu stock comments — replaces StockTwits Bullish/Bearish sentiment.

    Args:
        ticker: A-share symbol
        limit: Max comments to return

    Returns:
        Formatted text block with sentiment-like data.
    """
    code = _bare_code(ticker)
    try:
        import akshare as ak

        df = ak.stock_comment_em(symbol=code)
        if df is None or df.empty:
            return _placeholder(f"东方财富股吧: No comments for {code}")

        lines = [
            f"## 东方财富股吧讨论 — {code}",
            f"来源: 东方财富 (guba.eastmoney.com) · {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]
        bullish = 0
        bearish = 0
        neutral = 0
        for _, row in df.head(limit).iterrows():
            title = str(row.get("评论标题", row.get("title", "")))
            content = str(row.get("评论内容", row.get("content", "")))
            read_count = row.get("阅读数", row.get("read_count", 0))
            # Simple sentiment heuristic based on keywords
            text = (title + " " + content).lower()
            if any(k in text for k in ["涨", "买", "多", "牛", "利好", "突破"]):
                bullish += 1
            elif any(k in text for k in ["跌", "卖", "空", "熊", "利空", "风险"]):
                bearish += 1
            else:
                neutral += 1
            snippet = (title or content)[:100]
            if snippet:
                lines.append(f"- {snippet} (阅读:{read_count})")
                lines.append("")

        total = max(bullish + bearish + neutral, 1)
        lines.append(f"---")
        lines.append(f"情绪统计: 🟢看涨 {bullish} | ⚪中性 {neutral} | 🔴看空 {bearish}")
        lines.append(f"看涨比: {bullish/total*100:.0f}% ({bullish}/{total})")

        return "\n".join(lines)

    except ImportError:
        return _placeholder("东方财富股吧: Library not available")
    except Exception as e:
        logger.warning(f"EastMoney comments fetch failed for {ticker}: {e}")
        return _placeholder(f"东方财富股吧: Fetch failed ({e})")


def _placeholder(reason: str) -> str:
    """Return a placeholder block when data is unavailable."""
    return (
        f"<unavailable>\n"
        f"Chinese sentiment data unavailable for this ticker.\n"
        f"Reason: {reason}\n"
        f"</unavailable>"
    )
