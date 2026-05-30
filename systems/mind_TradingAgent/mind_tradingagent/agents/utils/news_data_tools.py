from langchain_core.tools import tool
from typing import Annotated, Optional
from mind_tradingagent.dataflows.interface import route_to_vendor
from mind_tradingagent.dataflows.akshare import get_china_market_news

@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    return route_to_vendor("get_news", ticker, start_date, end_date)

@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[Optional[int], "Days to look back; omit to use the configured default"] = None,
    limit: Annotated[Optional[int], "Max articles to return; omit to use the configured default"] = None,
) -> str:
    """
    Retrieve global news data, plus China A-share market intelligence.

    Combines:
    - Global macro news (yfinance / alpha_vantage)
    - China policy news (新闻联播 via akshare)
    - China A-share major announcements and risk alerts (东方财富公告 via akshare)

    Uses the configured news_data vendor. Defaults for look_back_days and
    limit come from DEFAULT_CONFIG (global_news_lookback_days,
    global_news_article_limit); pass explicit values to override.

    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back; omit to inherit config
        limit (int): Maximum number of articles to return; omit to inherit config

    Returns:
        str: A formatted string containing global news + China market intelligence
    """
    # China market news (akshare-based: CCTV + A-share announcements)
    china_news = get_china_market_news(date_str=curr_date)

    # Global macro news (yfinance / alpha_vantage via vendor routing)
    global_news = route_to_vendor("get_global_news", curr_date, look_back_days, limit)

    if china_news and "unavailable" not in china_news.lower():
        return f"{china_news}\n\n---\n\n{global_news}"
    return global_news

@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker)
