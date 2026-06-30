"""
Backtest market data fallback — multi-source resilience.

Tries, in priority order:
  1. data_warehouse (WarehouseReader → 5-source fallback, local SQLite cache)
  2. unified_cache (OHLCV cache, fast local SQLite hit)
  3. stock_analysis.db (ML system's stock_daily table, last resort)

Usage:
    from src.market_data_fallback import get_pred_and_next_close_with_fallback
    result = get_pred_and_next_close_with_fallback(
        conn_cache, date, stock_code, CACHE_DB
    )
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Global fallback statistics (tracked for monitoring) ─────
FALLBACK_STATS = {
    "warehouse_hits": 0,
    "warehouse_misses": 0,
    "unified_cache_hits": 0,
    "unified_cache_misses": 0,
    "analysis_hits": 0,
    "analysis_misses": 0,
    "total_fallback_calls": 0,
    "all_exhausted": 0,
}


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def get_pred_and_next_close_with_fallback(
    conn_cache: sqlite3.Connection,
    date: str,
    stock_code: str,
    cache_db_path: Path,
) -> Optional[Dict]:
    """
    Multi-source T+1 market data query with automatic fallback.

    Tries, in priority order:
      1. data_warehouse (WarehouseReader → local SQLite cache → 5-source API fallback)
      2. unified_cache (fast OHLCV cache hit, with data quality guard)
      3. stock_analysis.db (ML system's stock_daily table)

    When data is found from the warehouse, it is written *back* into
    unified_cache so future queries hit cache directly.

    Returns the same dict format as _get_pred_and_next_close():
        {
            "pred_trade_date": str,
            "next_date":       str,
            "next_close":      float,
            "pct_chg":         float,
            "days_offset":     None (computed later),
        }
    """
    FALLBACK_STATS["total_fallback_calls"] += 1

    # ── Tier 1: data_warehouse (primary source) ──────────────
    from services.data_warehouse.warehouse import WarehouseReader

    try:
        reader = WarehouseReader()
        data = reader.get_daily(stock_code, days=365)
        if data and isinstance(data, list) and len(data) > 1:
            result = _find_next_trading_day(data, date)
            if result is not None:
                FALLBACK_STATS["warehouse_hits"] += 1
                # Write back to unified_cache for future use
                _sync_to_unified_cache(conn_cache, stock_code, data, cache_db_path)
                logger.info(
                    "[Fallback] warehouse hit for %s %s: next_date=%s",
                    stock_code, date, result["next_date"],
                )
                return result
    except Exception as exc:
        logger.warning("[Fallback] warehouse error for %s %s: %s",
                       stock_code, date, exc)

    FALLBACK_STATS["warehouse_misses"] += 1

    # ── Tier 2: unified_cache (fast OHLCV cache) ─────────────
    try:
        result = _query_unified_cache(conn_cache, stock_code, date)
        if result is not None:
            FALLBACK_STATS["unified_cache_hits"] += 1
            logger.info(
                "[Fallback] unified_cache hit for %s %s: next_date=%s",
                stock_code, date, result["next_date"],
            )
            return result
    except Exception as exc:
        logger.warning("[Fallback] unified_cache error for %s %s: %s",
                       stock_code, date, exc)

    FALLBACK_STATS["unified_cache_misses"] += 1

    # ── Tier 3: stock_analysis.db (ML system, last resort) ──
    try:
        result = _query_stock_analysis_db(stock_code, date)
        if result is not None:
            FALLBACK_STATS["analysis_hits"] += 1
            logger.info(
                "[Fallback] analysis DB hit for %s %s: next_date=%s",
                stock_code, date, result["next_date"],
            )
            return result
    except Exception as exc:
        logger.warning("[Fallback] analysis DB error for %s %s: %s",
                       stock_code, date, exc)

    FALLBACK_STATS["analysis_misses"] += 1

    # ── All sources exhausted ───────────────────────────────
    FALLBACK_STATS["all_exhausted"] += 1
    logger.warning(
        "[Fallback] ALL sources exhausted for %s %s",
        stock_code, date,
    )
    return None


# ═══════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════

def _find_next_trading_day(
    rows: list[Dict[str, Any]],
    ref_date: str,
) -> Optional[Dict[str, Any]]:
    """Given a sorted list of OHLCV dicts, find {pred_trade_date, next_date, ...}

    `rows` is assumed to be sorted ascending by date.
    Returns None if ref_date falls outside the data range or no next day exists.
    """
    if not rows:
        return None

    # Ensure sorted ascending
    sorted_rows = sorted(rows, key=lambda r: str(r.get("date", "")))

    # Find the latest trading day <= ref_date
    pred_row = None
    for r in reversed(sorted_rows):
        if str(r.get("date", "")) <= ref_date:
            pred_row = r
            break

    if pred_row is None:
        return None

    pred_trade_date = str(pred_row["date"])
    pred_close = float(pred_row.get("close", 0) or 0)
    if pred_close <= 0:
        return None

    # Find the next trading day > pred_trade_date
    next_row = None
    for r in sorted_rows:
        if str(r.get("date", "")) > pred_trade_date:
            next_row = r
            break

    if next_row is None:
        return None

    next_close = float(next_row.get("close", 0) or 0)
    if next_close <= 0:
        return None

    pct_chg = round((next_close - pred_close) / pred_close * 100, 4)

    return {
        "pred_trade_date": pred_trade_date,
        "next_date": str(next_row["date"]),
        "next_close": next_close,
        "pct_chg": pct_chg,
        "days_offset": None,
    }


def _query_unified_cache(
    conn_cache: sqlite3.Connection,
    stock_code: str,
    ref_date: str,
) -> Optional[Dict[str, Any]]:
    """
    Query unified_cache's daily_ohlcv table for T+1 data.

    Replicates _get_pred_and_next_close() logic including the data quality
    guard (A-share daily price limit validation).
    """
    # Find the latest trading day's close ON or BEFORE ref_date
    pred_row = conn_cache.execute("""
        SELECT date, close FROM daily_ohlcv
        WHERE stock_code = ? AND date <= ?
          AND close IS NOT NULL AND close > 0
        ORDER BY date DESC LIMIT 1
    """, (stock_code, ref_date)).fetchone()

    if pred_row is None:
        return None

    pred_trade_date = pred_row["date"]
    pred_close = pred_row["close"]

    # Find the next trading day AFTER pred_trade_date
    next_row = conn_cache.execute("""
        SELECT date, close FROM daily_ohlcv
        WHERE stock_code = ? AND date > ?
          AND close IS NOT NULL AND close > 0
        ORDER BY date ASC LIMIT 1
    """, (stock_code, pred_trade_date)).fetchone()

    if next_row is None:
        return None

    next_close = next_row["close"]
    pct_chg = round((next_close - pred_close) / pred_close * 100, 4)

    # ── Data quality guard: daily limit validation ───────────────
    # A-share daily limit varies by market segment:
    #   688xxx/689xxx 科创板(STAR)     ±20%
    #   300xxx/301xxx 创业板(ChiNext)  ±20%
    #   8xxxxx        北交所(BSE)      ±30%
    #   股票名含 *ST/ST               ±5%
    #   其他(主板)                     ±10%
    # Values beyond limit + 0.5% buffer indicate corrupted cache data.
    if not stock_code or not isinstance(stock_code, str):
        return None
    stock_code_str = str(stock_code).strip()
    if not re.match(r'^\d{6}', stock_code_str):
        return None
    if stock_code_str.startswith(("688", "689")):
        max_pct = 20.5  # @calibration 科创板 STAR ±20% + 0.5% buffer
    elif stock_code_str.startswith(("300", "301")):
        max_pct = 20.5  # @calibration 创业板 ChiNext ±20% + 0.5% buffer
    elif stock_code_str.startswith("8"):
        max_pct = 30.5  # @calibration 北交所 BSE ±30% + 0.5% buffer
    else:
        max_pct = 10.5  # @calibration 主板 ±10% + 0.5% buffer
    if abs(pct_chg) > max_pct:
        return None

    return {
        "pred_trade_date": pred_trade_date,
        "next_date": next_row["date"],
        "next_close": next_close,
        "pct_chg": pct_chg,
        "days_offset": None,
    }


def _query_stock_analysis_db(
    stock_code: str,
    ref_date: str,
) -> Optional[Dict[str, Any]]:
    """
    Query ML system's stock_analysis.db stock_daily table for T+1 data.

    Schema: stock_daily(code TEXT, date TEXT, close REAL, ...)
    """
    project_root = Path(__file__).resolve().parent.parent
    analysis_db = project_root / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"

    if not analysis_db.exists():
        return None

    conn = sqlite3.connect(str(analysis_db))
    conn.row_factory = sqlite3.Row
    try:
        # Find the latest trading day's close ON or BEFORE ref_date
        pred_row = conn.execute("""
            SELECT date, close FROM stock_daily
            WHERE code = ? AND date <= ?
              AND close IS NOT NULL AND close > 0
            ORDER BY date DESC LIMIT 1
        """, (stock_code, ref_date)).fetchone()

        if pred_row is None:
            return None

        pred_trade_date = pred_row["date"]
        pred_close = pred_row["close"]

        # Find the next trading day AFTER pred_trade_date
        next_row = conn.execute("""
            SELECT date, close FROM stock_daily
            WHERE code = ? AND date > ?
              AND close IS NOT NULL AND close > 0
            ORDER BY date ASC LIMIT 1
        """, (stock_code, pred_trade_date)).fetchone()

        if next_row is None:
            return None

        next_close = next_row["close"]
        pct_chg = round((next_close - pred_close) / pred_close * 100, 4)

        return {
            "pred_trade_date": pred_trade_date,
            "next_date": str(next_row["date"]),
            "next_close": next_close,
            "pct_chg": pct_chg,
            "days_offset": None,
        }
    finally:
        conn.close()


def _sync_to_unified_cache(
    conn_cache: sqlite3.Connection,
    stock_code: str,
    rows: list[Dict[str, Any]],
    cache_db_path: Path,
) -> int:
    """
    Write warehouse data rows back into unified_cache's daily_ohlcv table.
    Uses INSERT OR IGNORE to avoid overwriting existing (potentially newer) data.
    Returns the number of rows inserted.
    """
    inserted = 0
    import time
    now = time.time()

    for row in rows:
        try:
            conn_cache.execute("""
                INSERT OR IGNORE INTO daily_ohlcv
                    (stock_code, date, open, high, low, close,
                     volume, amount, pct_chg, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stock_code,
                str(row.get("date", "")),
                row.get("open"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("volume", 0),
                row.get("amount", 0),
                row.get("pct_chg", 0),
                "fallback_warehouse",
                now,
            ))
            inserted += 1
        except Exception:
            continue

    if inserted > 0:
        conn_cache.commit()

    return inserted


def get_fallback_stats() -> Dict[str, int]:
    """Return a copy of the current fallback statistics."""
    return dict(FALLBACK_STATS)


def reset_fallback_stats() -> None:
    """Reset all fallback counters (for testing / new sessions)."""
    for key in FALLBACK_STATS:
        FALLBACK_STATS[key] = 0
