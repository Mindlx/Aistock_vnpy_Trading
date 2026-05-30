"""Akshare data vendor for Chinese A-share markets.

Provides OHLCV, fundamentals, and news data via the akshare library.
Implements the same function signatures as y_finance.py for compatibility
with the vendor routing system in interface.py.

Usage: set data_vendors.core_stock_apis = "akshare" in default_config.py
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Optional

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)


def _bare_code(symbol: str) -> str:
    """Extract bare 6-digit A-share code from symbol (e.g. '601801.SS' -> '601801')."""
    return symbol.replace(".SS", "").replace(".SZ", "").replace(".SH", "").strip()


def _csv_string(df: pd.DataFrame, title: str) -> str:
    """Convert DataFrame to CSV string with header."""
    if df is None or df.empty:
        return f"# No data for {title}\n"
    header = f"# {title}\n# Total records: {len(df)}\n# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + df.to_csv()


def get_stock_data(
    symbol: Annotated[str, "stock code, e.g. 601801.SS for Shanghai, 000001.SZ for Shenzhen"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Get daily OHLCV data for an A-share stock via akshare."""
    code = _bare_code(symbol)
    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return f"# No data found for {symbol} between {start_date} and {end_date}\n"
        return _csv_string(df, f"A-share stock data for {code} ({symbol})")
    except Exception as e:
        logger.warning(f"akshare get_stock_data({symbol}) failed: {e}")
        return f"# Error fetching data for {symbol}: {e}\n"


def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to analyze"],
    curr_date: Annotated[str, "current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """Compute technical indicators from akshare daily data."""
    from datetime import timedelta
    code = _bare_code(symbol)
    start = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days * 2)).strftime("%Y-%m-%d")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start.replace("-", ""), adjust="qfq")
        if df is None or df.empty:
            return f"# No data for {symbol}\n"
        # Compute basic stats from available data
        closes = df["收盘"].astype(float)
        volumes = df["成交量"].astype(float)
        result = [
            f"# Technical Indicators for {code} ({symbol})",
            f"# Analysis date: {curr_date}",
            f"# Lookback: {look_back_days} days",
            f"# Data records: {len(df)}",
            "",
            f"Latest Close: {closes.iloc[-1]:.2f}",
            f"5-day change: {((closes.iloc[-1] / closes.iloc[-5]) - 1) * 100:.2f}%" if len(closes) >= 5 else "5-day change: N/A",
            f"20-day change: {((closes.iloc[-1] / closes.iloc[-20]) - 1) * 100:.2f}%" if len(closes) >= 20 else "20-day change: N/A",
            f"Max(20d): {closes.tail(20).max():.2f}",
            f"Min(20d): {closes.tail(20).min():.2f}",
            f"Avg Volume(20d): {volumes.tail(20).mean():.0f}",
            f"Latest Volume: {volumes.iloc[-1]:.0f}",
            f"Volume Ratio: {volumes.iloc[-1] / volumes.tail(20).mean():.2f}" if len(volumes) >= 20 else "Volume Ratio: N/A",
        ]
        return "\n".join(result)
    except Exception as e:
        logger.warning(f"akshare get_indicators({symbol}) failed: {e}")
        return f"# Error computing indicators for {symbol}: {e}\n"


def get_fundamentals(
    ticker: Annotated[str, "stock code, e.g. 601801.SS"],
    curr_date: Annotated[str, "current date"] = None,
) -> str:
    """Get fundamental data for an A-share stock via akshare."""
    code = _bare_code(ticker)
    try:
        # Financial analysis indicators (ROE, EPS, etc.)
        df = ak.stock_financial_analysis_indicator(symbol=code, start_year=datetime.now().year - 2)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            result = [
                f"# Fundamentals for {code} ({ticker})",
                f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
            ]
            for col in ["每股收益", "每股净资产", "净资产收益率", "总资产净利润率", "销售毛利率",
                         "营业利润率", "营业收入增长率", "净利润增长率", "资产负债率"]:
                if col in latest:
                    result.append(f"{col}: {latest[col]}")
            return "\n".join(result)
        return f"# No fundamental data for {code}\n"
    except Exception as e:
        logger.warning(f"akshare get_fundamentals({ticker}) failed: {e}")
        return f"# Error fetching fundamentals for {ticker}: {e}\n"


def get_balance_sheet(
    ticker: Annotated[str, "stock code, e.g. 601801.SS"],
    curr_date: Annotated[str, "current date"] = None,
) -> str:
    """Get balance sheet for an A-share stock."""
    code = _bare_code(ticker)
    try:
        df = ak.stock_financial_debt_ths(symbol=code)
        return _csv_string(df, f"Balance Sheet for {code}")
    except Exception:
        return f"# Balance sheet not available for {ticker}\n"


def get_cashflow(
    ticker: Annotated[str, "stock code, e.g. 601801.SS"],
    curr_date: Annotated[str, "current date"] = None,
) -> str:
    """Get cash flow statement for an A-share stock."""
    code = _bare_code(ticker)
    try:
        df = ak.stock_financial_cash_ths(symbol=code)
        return _csv_string(df, f"Cash Flow for {code}")
    except Exception:
        return f"# Cash flow not available for {ticker}\n"


def get_income_statement(
    ticker: Annotated[str, "stock code, e.g. 601801.SS"],
    curr_date: Annotated[str, "current date"] = None,
) -> str:
    """Get income statement for an A-share stock."""
    code = _bare_code(ticker)
    try:
        df = ak.stock_financial_benefit_ths(symbol=code)
        return _csv_string(df, f"Income Statement for {code}")
    except Exception:
        return f"# Income statement not available for {ticker}\n"


def get_news(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """Get news for an A-share stock."""
    code = _bare_code(ticker)
    try:
        df = ak.stock_info_news(symbol=code)
        if df is not None and not df.empty:
            lines = [f"# News for {code} ({ticker})", f"# Period: {start_date} to {end_date}", ""]
            for _, row in df.head(20).iterrows():
                title = row.get("新闻标题", row.get("title", ""))
                date = row.get("发布时间", row.get("date", ""))
                if title:
                    lines.append(f"- [{date}] {title}")
            return "\n".join(lines)
        return f"# No news found for {code}\n"
    except Exception as e:
        logger.warning(f"akshare get_news({ticker}) failed: {e}")
        return f"# News not available for {ticker}: {e}\n"


def get_global_news() -> str:
    """Get global/macro news. Akshare doesn't directly support this;
    falls back to mock data indicating availability.
    """
    return "# Global macro news: not available via akshare. Configure yfinance or alpha_vantage for global news.\n"


def get_insider_transactions(
    ticker: Annotated[str, "stock code, e.g. 601801.SS"]
) -> str:
    """Get insider transactions. A-share insider data is limited; return placeholder."""
    return f"# Insider transactions data not available for A-shares via akshare.\n"
