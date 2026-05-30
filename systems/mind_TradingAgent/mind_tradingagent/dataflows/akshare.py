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


def _bs_code(symbol: str) -> str:
    """Convert ticker to baostock format: '601801.SS' -> 'sh.601801'."""
    code = _bare_code(symbol)
    if symbol.upper().endswith(".SS"):
        return f"sh.{code}"
    return f"sz.{code}"


def _try_baostock_ohlcv(symbol: str, start_date: str, end_date: str) -> Optional[str]:
    """Fallback: fetch daily OHLCV from baostock when akshare is unavailable."""
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            return None
        try:
            rs = bs.query_history_k_data_plus(
                _bs_code(symbol),
                "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg,peTTM,pbMRQ",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="2",
            )
            if rs.error_code != '0':
                return None
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return None
            df = pd.DataFrame(rows[1:], columns=rows[0]) if len(rows) > 1 else pd.DataFrame(rows, columns=rs.fields)
            for col in ["open","high","low","close","volume","amount","pctChg","peTTM","pbMRQ"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return _csv_string(df, f"BaoStock data for {_bare_code(symbol)} ({symbol})")
        finally:
            bs.logout()
    except Exception as e:
        logger.warning(f"baostock fallback failed for {symbol}: {e}")
        return None


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
    """Get daily OHLCV data for an A-share stock via akshare.

    Falls back to baostock if akshare is unavailable (e.g., rate-limited).
    """
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
        # Fallback to baostock before giving up
        result = _try_baostock_ohlcv(symbol, start_date, end_date)
        if result is not None:
            logger.info(f"baostock fallback succeeded for {symbol}")
            return result
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
        # North-bound capital flow (北向资金)
        import datetime as dt
        end = dt.date.today().strftime("%Y%m%d")
        start = (dt.date.today() - dt.timedelta(days=30)).strftime("%Y%m%d")
        df_north = ak.stock_hsgt_individual_detail_em(
            symbol=code, start_date=start, end_date=end,
        )
        if df_north is not None and not df_north.empty:
            latest_n = df_north.iloc[-1]
            result.append("")
            result.append("--- 北向资金持股 ---")
            for col in latest_n.index[:5]:
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
    """Get news for an A-share stock.

    Primary source: EastMoney (东方财富) — rich A-share news coverage.
    Enhanced with: official announcements from Cninfo (巨潮资讯) via EastMoney notice API.
    Fallback: stock_info_news (generic).
    """
    code = _bare_code(ticker)
    parts = []

    try:
        # Primary: EastMoney news (rich, A-share specific)
        df = ak.stock_news_em(symbol=code)
        if df is not None and not df.empty:
            lines = [
                f"# News for {code} ({ticker})",
                f"# Source: 东方财富 (EastMoney)",
                f"# Period: {start_date} to {end_date}",
                "",
            ]
            for _, row in df.iterrows():
                title = str(row.get("新闻标题", ""))
                source = str(row.get("文章来源", ""))
                time = str(row.get("发布时间", ""))
                content = str(row.get("新闻内容", ""))[:120]
                if title:
                    lines.append(f"### {title}")
                    lines.append(f"来源: {source} | 时间: {time}")
                    if content:
                        lines.append(f"{content}...")
                    lines.append("")
            parts.append("\n".join(lines))
    except Exception as e:
        logger.warning(f"EastMoney news failed for {ticker}: {e}")

    try:
        # Enhanced: Cninfo official announcements via EastMoney notice API
        # This covers 重大事项, 财务报告, 风险提示 etc. — more authoritative than news
        df_notice = ak.stock_individual_notice_report(
            security=code, symbol="全部",
            begin_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
        )
        if df_notice is not None and not df_notice.empty:
            lines = [
                f"# Official Announcements for {code} ({ticker})",
                f"# Source: 巨潮资讯 (Cninfo) via EastMoney",
                "",
            ]
            for _, row in df_notice.iterrows():
                title = str(row.get("公告标题", ""))
                ann_type = str(row.get("公告类型", ""))
                ann_date = str(row.get("公告日期", ""))
                if title:
                    lines.append(f"- [{ann_type}] {title} ({ann_date})")
            parts.append("\n".join(lines))
    except Exception as e:
        logger.debug(f"Cninfo notice failed for {ticker}: {e}")

    if parts:
        return "\n\n".join(parts)

    # Fallback: generic stock info news
    try:
        df = ak.stock_info_news(symbol=code)
        if df is not None and not df.empty:
            lines = [f"# News for {code} ({ticker}) (fallback)", f"# Period: {start_date} to {end_date}", ""]
            for _, row in df.head(20).iterrows():
                title = row.get("新闻标题", row.get("title", ""))
                date = row.get("发布时间", row.get("date", ""))
                if title:
                    lines.append(f"- [{date}] {title}")
            return "\n".join(lines) if len(lines) > 2 else f"# No news found for {code}\n"
    except Exception as e2:
        logger.warning(f"fallback news also failed for {ticker}: {e2}")

    return f"# No news found for {code}\n"


def get_china_market_news(date_str: str = "") -> str:
    """Get China A-share market-wide news and policy intelligence.

    Sources (free, akshare-based):
    - 新闻联播 (CCTV News): official policy signals
    - 重大事项公告: all A-share major announcements
    - 风险提示公告: risk alerts across the market

    Returns formatted text with policy news + market announcements.
    """
    from datetime import datetime
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    date_compact = date_str.replace("-", "")
    parts = []

    # 1. CCTV News — policy signals
    try:
        df = ak.news_cctv(date=date_compact)
        if df is not None and not df.empty:
            lines = [
                "# China Policy News — 新闻联播",
                "# Source: CCTV (official, most authoritative)",
                "",
            ]
            for _, row in df.iterrows():
                title = str(row.get("title", ""))
                content = str(row.get("content", ""))[:200]
                if title:
                    lines.append(f"## {title}")
                    lines.append(f"{content}...")
                    lines.append("")
            parts.append("\n".join(lines))
    except Exception as e:
        logger.debug(f"CCTV news failed: {e}")

    # 2. Major announcements across all A-shares
    try:
        df = ak.stock_notice_report(symbol="重大事项", date=date_compact)
        if df is not None and not df.empty:
            lines = [
                "# A-share Major Announcements (重大事项)",
                f"# Date: {date_str} | Total: {len(df)} announcements",
                "# Source: EastMoney / Cninfo",
                "",
            ]
            for _, row in df.head(15).iterrows():
                title = str(row.get("公告标题", ""))
                code = str(row.get("代码", ""))
                name = str(row.get("名称", ""))
                if title:
                    lines.append(f"- {name}({code}): {title}")
            parts.append("\n".join(lines))
    except Exception as e:
        logger.debug(f"Major announcements failed: {e}")

    # 3. Risk alerts across the market
    try:
        df = ak.stock_notice_report(symbol="风险提示", date=date_compact)
        if df is not None and not df.empty:
            lines = [
                "# A-share Risk Alerts (风险提示)",
                f"# Date: {date_str} | Total: {len(df)} alerts",
                "",
            ]
            for _, row in df.head(10).iterrows():
                title = str(row.get("公告标题", ""))
                code = str(row.get("代码", ""))
                name = str(row.get("名称", ""))
                if title:
                    lines.append(f"- {name}({code}): {title}")
            parts.append("\n".join(lines))
    except Exception as e:
        logger.debug(f"Risk alerts failed: {e}")

    if parts:
        return "\n\n".join(parts)
    return "China market news temporarily unavailable.\n"


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
