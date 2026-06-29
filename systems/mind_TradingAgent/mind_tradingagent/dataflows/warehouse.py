"""Warehouse data vendor — A-share data via services.data_warehouse.

Replaces yfinance/alpha_vantage with our own data warehouse (cache-first,
multi-source fallback chain: Tushare → pytdx → Sina → akshare → efinance).

Implements the same function signatures as y_finance.py / akshare.py for
compatibility with the vendor routing system in interface.py.

Usage:
    set data_vendors.core_stock_apis = "warehouse" in default_config.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Annotated, Any

import pandas as pd

from dateutil.relativedelta import relativedelta

from .errors import NoMarketDataError
from .stockstats_utils import _assert_ohlcv_not_stale

logger = logging.getLogger(__name__)

# ── Helper ──


def _bare_code(symbol: str) -> str:
    """Extract bare 6-digit A-share code (e.g. '601801.SS' -> '601801')."""
    return symbol.replace(".SS", "").replace(".SZ", "").replace(".SH", "").strip()


def _get_reader():
    """Lazy import WarehouseReader so the module never crashes on import."""
    from services.data_warehouse import WarehouseReader
    return WarehouseReader()


def _df_to_csv(df: pd.DataFrame, label: str) -> str:
    """Format a DataFrame to CSV string with a header line (matching yfinance style)."""
    header = f"# {label}\n"
    header += f"# Total records: {len(df)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + df.to_csv()


def _dict_to_markdown(data: dict, title: str) -> str:
    """Render a flat dict as a markdown section."""
    lines = [f"### {title}", ""]
    for k, v in data.items():
        lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


# ═══════════════════════════════════════════
# 1. OHLCV stock data
# ═══════════════════════════════════════════


def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Fetch historical OHLCV data from data warehouse."""
    code = _bare_code(symbol)
    reader = _get_reader()
    df = reader.get_daily_df(code, start=start_date, end=end_date)
    if df.empty:
        raise NoMarketDataError(
            symbol, code,
            f"no OHLCV data between {start_date} and {end_date} in warehouse",
        )
    return _df_to_csv(df, f"Stock data for {code} from {start_date} to {end_date}")


# ═══════════════════════════════════════════
# 2. Technical indicators (via stockstats)
# ═══════════════════════════════════════════


# Supported indicators with descriptions (mirrors y_finance.py's best_ind_params)
_INDICATOR_DESCRIPTIONS = {
    "close_50_sma": "50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance.",
    "close_200_sma": "200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups.",
    "close_10_ema": "10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points.",
    "macd": "MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes.",
    "macds": "MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades.",
    "macdh": "MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early.",
    "rsi": "RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence.",
    "boll": "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement.",
    "boll_ub": "Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions.",
    "boll_lb": "Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions.",
    "atr": "ATR: Averages true range to measure volatility. Usage: Set stop-loss levels based on current market volatility.",
    "vwma": "VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data.",
    "mfi": "MFI: Money Flow Index uses price and volume to measure buying/selling pressure. Usage: Identify overbought (>80) or oversold (<20) conditions.",
}


def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator name"],
    curr_date: Annotated[str, "The current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """Compute technical indicators from warehouse OHLCV via stockstats.

    Mirrors y_finance.py get_stock_stats_indicators_window() output format.
    """
    code = _bare_code(symbol)
    reader = _get_reader()

    # Fetch extra data for MA200 etc.
    df = reader.get_daily_df(code, days=look_back_days + 200)
    if df.empty:
        raise NoMarketDataError(
            symbol, code,
            "no OHLCV data available for indicator calculation",
        )

    # Rename columns for stockstats (expects Open/High/Low/Close/Volume)
    col_map = {"date": "Date", "open": "Open", "high": "High",
               "low": "Low", "close": "Close", "volume": "Volume"}
    df = df.rename(columns=col_map)

    # Ensure datetime and set index
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()

    # Reject stale data
    _assert_ohlcv_not_stale(df, curr_date, symbol, code)

    # Wrap with stockstats for indicator calculation
    from stockstats import wrap
    ss_df = wrap(df)

    # stockstats adds a second-level column index; reindex for flat access
    if isinstance(ss_df.columns, pd.MultiIndex):
        ss_df.columns = ss_df.columns.get_level_values(1)

    ss_df["Date"] = ss_df.index

    # Calculate indicator — trigger stockstats to compute it
    try:
        ss_df[indicator]
    except Exception:
        pass  # stockstats computes on access

    # Build date range
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - timedelta(days=look_back_days)
    lines = []
    d = curr_dt
    while d >= start_dt:
        ds = d.strftime("%Y-%m-%d")
        matching = ss_df[ss_df["Date"] == ds]
        if not matching.empty:
            val = matching[indicator].iloc[0]
            if pd.isna(val):
                lines.append(f"{ds}: N/A")
            else:
                lines.append(f"{ds}: {val}")
        else:
            lines.append(f"{ds}: N/A: Not a trading day (weekend or holiday)")
        d -= timedelta(days=1)

    description = _INDICATOR_DESCRIPTIONS.get(indicator, "No description available.")
    result = (
        f"## {indicator} values from {start_dt.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + description
    )
    return result


# ═══════════════════════════════════════════
# 3. Fundamentals snapshot
# ═══════════════════════════════════════════


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "date of the analysis, YYYY-mm-dd"],
) -> str:
    """Fetch fundamentals (market cap, PE/PB, ROE, industry, etc.)."""
    code = _bare_code(ticker)
    reader = _get_reader()

    parts = []

    # 1. Fundamentals snapshot (market cap, industry, etc.)
    fund = reader.get_fundamentals(code)
    if fund:
        parts.append(_dict_to_markdown(fund, f"Fundamentals for {code}"))

    # 2. PE / PB
    pepb = reader.get_pe_pb(code)
    if pepb:
        parts.append(_dict_to_markdown(pepb, "Valuation"))

    # 3. Financial indicators (ROE, EPS, etc.)
    fin = reader.get_financial(code)
    if fin:
        # fin is {indicator_name: {period: value}} — flatten latest period
        rows = []
        for indicator_name, periods in fin.items():
            # pick the most recent period
            sorted_periods = sorted(periods.keys(), reverse=True)
            if sorted_periods:
                rows.append(f"- **{indicator_name}**: {periods[sorted_periods[0]]} ({sorted_periods[0]})")
        if rows:
            parts.append("### Financial Indicators\n" + "\n".join(rows))

    if not parts:
        raise NoMarketDataError(
            ticker, code,
            "no fundamentals data available in warehouse",
        )

    return "\n\n".join(parts)


# ═══════════════════════════════════════════
# 4–6. Financial statements (partial via warehouse, fallback to akshare)
# ═══════════════════════════════════════════


def _try_akshare_financial(code: str, table_type: str) -> str | None:
    """Fallback: fetch financial statement via akshare."""
    try:
        import akshare as ak
        if table_type == "balance_sheet":
            df = ak.stock_financial_debt_ths(symbol=code)
        elif table_type == "cashflow":
            df = ak.stock_financial_cash_ths(symbol=code)
        elif table_type == "income":
            df = ak.stock_financial_report_sina(symbol=code)
        else:
            return None
        if df is not None and not df.empty:
            return _df_to_csv(df.head(20), f"{table_type} for {code} (via akshare)")
    except Exception as exc:
        logger.debug("akshare fallback failed for %s %s: %s", code, table_type, exc)
    return None


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency: Q (quarterly) or A (annual)"],
    curr_date: Annotated[str, "date of the analysis, YYYY-mm-dd"],
) -> str:
    """Fetch balance sheet — warehouse partial data first, then akshare fallback."""
    code = _bare_code(ticker)
    reader = _get_reader()

    # First try: get what we have from financial indicators
    fin = reader.get_financial(code)
    if fin:
        parts = [f"### Balance Sheet (partial) for {code}"]
        for indicator_name, periods in fin.items():
            sorted_periods = sorted(periods.keys(), reverse=True)
            if sorted_periods:
                parts.append(f"- **{indicator_name}**: {periods[sorted_periods[0]]} ({sorted_periods[0]})")
        return "\n".join(parts)

    # Fallback to akshare
    result = _try_akshare_financial(code, "balance_sheet")
    if result:
        return result

    raise NoMarketDataError(
        ticker, code,
        "balance sheet not available from warehouse or akshare fallback",
    )


def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency: Q (quarterly) or A (annual)"],
    curr_date: Annotated[str, "date of the analysis, YYYY-mm-dd"],
) -> str:
    """Fetch cash flow statement — akshare fallback."""
    code = _bare_code(ticker)
    result = _try_akshare_financial(code, "cashflow")
    if result:
        return result
    raise NoMarketDataError(
        ticker, code,
        "cash flow statement not available from warehouse or akshare fallback",
    )


def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency: Q (quarterly) or A (annual)"],
    curr_date: Annotated[str, "date of the analysis, YYYY-mm-dd"],
) -> str:
    """Fetch income statement — akshare fallback."""
    code = _bare_code(ticker)
    result = _try_akshare_financial(code, "income")
    if result:
        return result
    raise NoMarketDataError(
        ticker, code,
        "income statement not available from warehouse or akshare fallback",
    )


# ═══════════════════════════════════════════
# 7–8. News
# ═══════════════════════════════════════════


def get_news(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Fetch stock-specific news from warehouse."""
    code = _bare_code(ticker)
    reader = _get_reader()
    items = reader.get_news(code, days=14, limit=30)
    if not items:
        raise NoMarketDataError(
            ticker, code,
            "no news data available in warehouse",
        )
    lines = [f"# News for {code}", ""]
    for item in items:
        title = item.get("title", "Untitled")
        date = item.get("date", "")
        source = item.get("source", "")
        content = item.get("content", "")
        lines.append(f"### {title}")
        if source or date:
            lines.append(f"来源: {source} | {date}")
        if content:
            # Truncate long content to keep tool output manageable
            lines.append(content[:500] + ("…" if len(content) > 500 else ""))
        lines.append("")
    return "\n".join(lines)


def get_global_news(
    curr_date: Annotated[str, "The current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
    limit: Annotated[int, "limit for number of news"],
) -> str:
    """Global/macro news — delegate to akshare China market news where possible."""
    try:
        import akshare as ak
        df = ak.stock_info_global_cls()
        if df is not None and not df.empty:
            return _df_to_csv(df.head(limit or 10), "Global Market News (via akshare)")
    except Exception as exc:
        logger.debug("akshare global news failed: %s", exc)
    return (
        "DATA_UNAVAILABLE: Global market news not available from data warehouse. "
        "Proceed without it; do not fabricate values."
    )


# ═══════════════════════════════════════════
# 9. Insider transactions
# ═══════════════════════════════════════════


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Fetch insider/shareholder changes — akshare fallback."""
    code = _bare_code(ticker)
    try:
        import akshare as ak
        df = ak.stock_shareholder_change_em(symbol=code)
        if df is not None and not df.empty:
            return _df_to_csv(df.head(10), f"Shareholder changes for {code} (via akshare)")
    except Exception as exc:
        logger.debug("akshare insider tx failed for %s: %s", code, exc)

    return (
        f"NO_DATA_AVAILABLE: Insider/shareholder transaction data for '{code}' "
        "not available from data warehouse."
    )


# ═══════════════════════════════════════════
# 12. Capital flows
# ═══════════════════════════════════════════


def get_capital_flows(
    ticker: Annotated[str, "ticker symbol of the company"],
    days: Annotated[int, "Number of trading days to look back (default 30)"] = 30,
) -> str:
    """Fetch capital flow data (主力净流入/北向资金/大单小单) from data warehouse.

    Returns a Markdown table with daily main-force net flows, super-large/large/medium/small
    order flows, and a summary of the multi-day trend.
    """
    code = _bare_code(ticker)
    reader = _get_reader()
    rows = reader.get_capital_flows(code, days=days)
    if not rows:
        raise NoMarketDataError(
            ticker, code,
            "no capital flow data available in warehouse",
        )
    lines = [f"# Capital Flows for {code} (past {days} trading days)", ""]
    lines.append(
        "| Date | Main Net Flow | Super Large Net | Large Net | "
        "Medium Net | Small Net | Source |"
    )
    lines.append(
        "|------|--------------|----------------|-----------|"
        "------------|-----------|--------|"
    )
    for row in rows:
        lines.append(
            f"| {row['date']} "
            f"| {row['main_net_flow']:,.0f} "
            f"| {row['super_large_net']:,.0f} "
            f"| {row['large_net']:,.0f} "
            f"| {row['medium_net']:,.0f} "
            f"| {row['small_net']:,.0f} "
            f"| {row['source']} |"
        )
    total_main = sum(r["main_net_flow"] for r in rows)
    avg_main = total_main / len(rows)
    latest = rows[-1]
    lines.append("")
    lines.append(
        f"**Summary**: Total main net flow over period: {total_main:,.0f} | "
        f"Avg daily: {avg_main:,.0f} | "
        f"Latest ({latest['date']}): {latest['main_net_flow']:,.0f}"
    )
    return "\n".join(lines)


# ═══════════════════════════════════════════
# 10. Macro indicators — not applicable for A-share
# ═══════════════════════════════════════════


def get_macro_indicators(
    indicator: Annotated[str, "macroeconomic indicator name"],
    curr_date: Annotated[str, "The current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """Macro indicators (FRED) — not applicable for A-share analysis."""
    return (
        "DATA_UNAVAILABLE: FRED macroeconomic indicators are not applicable "
        "for China A-share market analysis. Proceed without it; do not fabricate values."
    )


# ═══════════════════════════════════════════
# 11. Prediction markets — not applicable for A-share
# ═══════════════════════════════════════════


def get_prediction_markets(
    topic: Annotated[str, "topic to search for"],
    limit: Annotated[int, "limit on the number of events to return"],
) -> str:
    """Prediction markets (Polymarket) — not applicable for A-share."""
    return (
        "DATA_UNAVAILABLE: Polymarket prediction markets are not applicable "
        "for China A-share analysis. Proceed without it; do not fabricate values."
    )
