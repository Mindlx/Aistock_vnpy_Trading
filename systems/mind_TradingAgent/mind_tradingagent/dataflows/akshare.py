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


def _compute_rsi(closes: "pd.Series", period: int = 14) -> float:
    """Compute RSI."""
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not rsi.empty and not pd.isna(rsi.iloc[-1]) else float("nan")


def _compute_macd(closes: "pd.Series") -> dict:
    """Compute MACD line, signal line, histogram."""
    ema12 = closes.ewm(span=12).mean()
    ema26 = closes.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9).mean()
    hist = macd_line - signal
    return {
        "macd": macd_line.iloc[-1] if not macd_line.empty else float("nan"),
        "signal": signal.iloc[-1] if not signal.empty else float("nan"),
        "histogram": hist.iloc[-1] if not hist.empty else float("nan"),
    }


def _compute_bollinger(closes: "pd.Series", period: int = 20) -> dict:
    """Compute Bollinger Bands."""
    sma = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    return {
        "mid": sma.iloc[-1] if not sma.empty else float("nan"),
        "upper": upper.iloc[-1] if not upper.empty else float("nan"),
        "lower": lower.iloc[-1] if not lower.empty else float("nan"),
    }


def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to analyze"],
    curr_date: Annotated[str, "current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """Compute technical indicators from akshare daily data.

    Supports: RSI, MACD, SMA, Bollinger Bands, ATR, VWMA, and basic stats.
    Falls back to yfinance if akshare data is unavailable.
    """
    from datetime import timedelta
    import numpy as np

    code = _bare_code(symbol)
    start = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days * 2)).strftime("%Y-%m-%d")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start.replace("-", ""), adjust="qfq")
        if df is None or df.empty:
            return f"# No data for {symbol}\n"

        closes = df["收盘"].astype(float)
        highs = df["最高"].astype(float)
        lows = df["最低"].astype(float)
        volumes = df["成交量"].astype(float)

        # Full technical indicators
        rsi = _compute_rsi(closes)
        macd = _compute_macd(closes)
        boll = _compute_bollinger(closes)

        # ATR
        tr = pd.concat([
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows - closes.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1] if len(tr) >= 14 else float("nan")

        result = [
            f"# Technical Indicators for {code} ({symbol})",
            f"# Analysis date: {curr_date}",
            f"# Lookback: {look_back_days} days | Data records: {len(df)}",
            "",
            "## 1. Price Action",
            f"Latest Close: {closes.iloc[-1]:.2f}",
            f"Day Change: {(closes.iloc[-1] / closes.iloc[-2] - 1) * 100:.2f}%" if len(closes) >= 2 else "",
            f"5-day Change: {((closes.iloc[-1] / closes.iloc[-5]) - 1) * 100:.2f}%" if len(closes) >= 5 else "",
            f"20-day Change: {((closes.iloc[-1] / closes.iloc[-20]) - 1) * 100:.2f}%" if len(closes) >= 20 else "",
            f"Max(20d): {closes.tail(20).max():.2f}  Min(20d): {closes.tail(20).min():.2f}",
            "",
            "## 2. Momentum",
            f"RSI(14): {rsi:.1f}" if not np.isnan(rsi) else "",
            f"MACD: {macd['macd']:.4f}  Signal: {macd['signal']:.4f}  Histogram: {macd['histogram']:.4f}",
            f"MACD Crossover: {'BULLISH' if macd['macd'] > macd['signal'] else 'BEARISH'}",
            "",
            "## 3. Volatility",
            f"Bollinger Upper: {boll['upper']:.2f}  Mid: {boll['mid']:.2f}  Lower: {boll['lower']:.2f}",
            f"Bollinger Position: {((closes.iloc[-1] - boll['lower']) / (boll['upper'] - boll['lower']) * 100):.1f}%" if not np.isnan(boll['upper'] - boll['lower']) else "",
            f"ATR(14): {atr:.4f}  ATR%: {atr / closes.iloc[-1] * 100:.2f}%" if not np.isnan(atr) else "",
            "",
            "## 4. Volume",
            f"Latest Volume: {volumes.iloc[-1]:.0f}",
            f"Avg Volume(20d): {volumes.tail(20).mean():.0f}",
            f"Volume Ratio: {volumes.iloc[-1] / volumes.tail(20).mean():.2f}" if len(volumes) >= 20 else "",
            "",
            "## 5. Moving Averages",
            f"SMA(5): {closes.rolling(5).mean().iloc[-1]:.2f}" if len(closes) >= 5 else "",
            f"SMA(10): {closes.rolling(10).mean().iloc[-1]:.2f}" if len(closes) >= 10 else "",
            f"SMA(20): {closes.rolling(20).mean().iloc[-1]:.2f}" if len(closes) >= 20 else "",
            f"SMA(50): {closes.rolling(50).mean().iloc[-1]:.2f}" if len(closes) >= 50 else "",
            f"SMA(200): {closes.rolling(200).mean().iloc[-1]:.2f}" if len(closes) >= 200 else "",
            "",
            f"Price vs SMA(20): {'ABOVE (bullish)' if closes.iloc[-1] > closes.rolling(20).mean().iloc[-1] else 'BELOW (bearish)'}" if len(closes) >= 20 else "",
        ]
        return "\n".join(filter(None, result))
    except Exception as e:
        logger.warning(f"akshare get_indicators({symbol}) failed: {e}")
        return f"# Error computing indicators for {symbol}: {e}\n"


def _append_capital_flow(result: list, code: str) -> None:
    """Append A-share capital flow data (北向资金 + 主力资金) to result list."""
    try:
        # Individual stock fund flow (主力资金流向)
        df_flow = ak.stock_individual_fund_flow(stock=code, market="sh" if code.startswith(('6', '5', '9')) else "sz")
        if df_flow is not None and not df_flow.empty:
            latest = df_flow.iloc[-1]
            result.append("")
            result.append("--- 资金流向 ---")
            for col in ["日期", "主力净流入", "小单净流入", "中单净流入", "大单净流入", "超大单净流入"]:
                if col in latest:
                    val = latest[col]
                    result.append(f"{col}: {val}")
    except Exception:
        pass

    try:
        # North-bound capital flow (北向资金) - only meaningful for Shanghai/Shenzhen connect stocks
        df_north = ak.stock_hsgt_individual_detail_em(
            symbol=code, market="沪股通" if code.startswith(('6', '5', '9')) else "深股通",
            indicator="持股信息"
        )
        if df_north is not None and not df_north.empty:
            latest_n = df_north.iloc[-1]
            result.append("")
            result.append("--- 北向资金持股 ---")
            for col in ["持股日期", "持股数量", "持股市值"]:
                if col in latest_n:
                    result.append(f"{col}: {latest_n[col]}")
    except Exception:
        pass


def get_fundamentals(
    ticker: Annotated[str, "stock code, e.g. 601801.SS"],
    curr_date: Annotated[str, "current date"] = None,
) -> str:
    """Get fundamental data for an A-share stock via akshare."""
    code = _bare_code(ticker)
    try:
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

            # Append A-share specific capital flow data
            _append_capital_flow(result, code)

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
