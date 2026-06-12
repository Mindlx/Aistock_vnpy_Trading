#!/usr/bin/env python3
"""
Unified Cache CLI — inspect, pre-warm, and manage the shared data cache.

Usage:
    python scripts/cache_cli.py stats                    # Show cache statistics
    python scripts/cache_cli.py warm --all               # Pre-warm all stocks from MindLynx DB
    python scripts/cache_cli.py warm --tradingagent       # Import from TradingAgent CSVs
    python scripts/cache_cli.py warm --lynx               # Fetch all from Sina (careful: hits API)
    python scripts/cache_cli.py clear 601801              # Clear one stock
    python scripts/cache_cli.py clear --all               # Clear entire cache
    python scripts/cache_cli.py clear --expired           # Remove only expired rows
    python scripts/cache_cli.py ls                        # List cached stocks

⚠️ 仅供学习和研究目的
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from src.unified_cache import UnifiedCache

# ── Config ──
DEFAULT_DB_PATH = "data/unified_cache/ohlcv_cache.db"
STOCK_POOL_PATH = "config/stock_pool.csv"
MINDLYNX_DB_PATH = "systems/MindLynx-Aistock/data/stock_analysis.db"
TRADINGAGENT_CACHE_DIR = os.path.expanduser("~/.mind_tradingagent/cache")


def _load_stock_codes() -> list[str]:
    """Read stock codes from stock_pool.csv."""
    codes = []
    with open(STOCK_POOL_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for row in reader:
            code = (row.get("code") or "").strip()
            if code and not code.startswith("#"):
                codes.append(code)
    return codes


# ═══════════════════════════════════════
# stats
# ═══════════════════════════════════════

def cmd_stats(db_path: str) -> None:
    cache = UnifiedCache(db_path=db_path)
    stats = cache.get_stats()
    print(f"📊 Unified Cache Statistics")
    print(f"   DB: {stats['db_path']}")
    print(f"   Total rows: {stats['total_rows']}")
    print(f"   Cached stocks: {len(stats['cached_stocks'])} — {', '.join(stats['cached_stocks'])}")
    print(f"   Sources: {stats['sources']}")
    print(f"\n   Per-stock metadata:")
    for m in stats["meta"]:
        age_min = m["age_s"] / 60
        print(f"     {m['code']:>8s}  {m['type']:>15s}  {m['rows']:>4d} rows  "
              f"age={age_min:.0f}min  source={m['source']}")


# ═══════════════════════════════════════
# ls
# ═══════════════════════════════════════

def cmd_ls(db_path: str) -> None:
    cache = UnifiedCache(db_path=db_path)
    stats = cache.get_stats()
    if not stats["cached_stocks"]:
        print("(empty)")
        return
    for code in sorted(stats["cached_stocks"]):
        fresh = cache.is_fresh(code)
        marker = "✅" if fresh else "⏳"
        print(f"  {marker} {code}")


# ═══════════════════════════════════════
# warm
# ═══════════════════════════════════════

def cmd_warm_mindlynx(cache: UnifiedCache, codes: list[str]) -> int:
    """Import OHLCV data from MindLynx-Aistock's SQLite database."""
    if not os.path.exists(MINDLYNX_DB_PATH):
        print(f"❌ MindLynx DB not found: {MINDLYNX_DB_PATH}")
        return 0

    conn = sqlite3.connect(MINDLYNX_DB_PATH)
    count = 0
    for code in codes:
        try:
            # MindLynx stores daily data in table named by stock code or "daily_{code}"
            table_candidates = [f"daily_{code}", code, f"stock_{code}"]
            df = None
            for table in table_candidates:
                try:
                    df = pd.read_sql_query(
                        f"SELECT date, open, high, low, close, volume, amount, pct_chg "
                        f"FROM [{table}] ORDER BY date DESC LIMIT 120",
                        conn,
                    )
                    if not df.empty:
                        break
                except (pd.io.sql.DatabaseError, sqlite3.OperationalError):
                    continue

            if df is None or df.empty:
                print(f"  ⏭  {code}: no data in MindLynx DB")
                continue

            n = cache.put_daily_ohlcv(code, df, source="mindlynx")
            print(f"  ✅ {code}: {n} rows from MindLynx")
            count += 1
        except Exception as e:
            print(f"  ⚠️  {code}: {e}")

    conn.close()
    return count


def cmd_warm_tradingagent(cache: UnifiedCache, codes: list[str]) -> int:
    """Import OHLCV data from TradingAgent's CSV cache files."""
    if not os.path.isdir(TRADINGAGENT_CACHE_DIR):
        print(f"❌ TradingAgent cache dir not found: {TRADINGAGENT_CACHE_DIR}")
        return 0

    count = 0
    for code in codes:
        # TradingAgent format: {code}.SZ-YFin-data-{start}-{end}.csv or {code}.SS-...
        suffixes = [".SZ", ".SS"]
        found = False
        for suffix in suffixes:
            pattern = f"{code}{suffix}-YFin-data-"
            for fname in os.listdir(TRADINGAGENT_CACHE_DIR):
                if fname.startswith(pattern) and fname.endswith(".csv"):
                    fpath = os.path.join(TRADINGAGENT_CACHE_DIR, fname)
                    try:
                        df = pd.read_csv(fpath, on_bad_lines="skip", encoding="utf-8")
                        if not df.empty:
                            n = cache.put_daily_ohlcv(
                                code, df,
                                source="yfinance",
                                target_date_col="Date",
                            )
                            print(f"  ✅ {code}: {n} rows from TradingAgent ({fname})")
                            count += 1
                            found = True
                            break
                    except Exception as e:
                        print(f"  ⚠️  {code}/{fname}: {e}")
            if found:
                break
        if not found:
            print(f"  ⏭  {code}: no TradingAgent cache file")

    return count


def cmd_warm_lynx(cache: UnifiedCache, codes: list[str]) -> int:
    """Fetch all stocks from Sina API and cache them (careful: hits live API)."""
    sys.path.insert(0, "systems/lynx_vnpy")
    try:
        import lynx_signal
    except ImportError:
        print("❌ Cannot import lynx_signal")
        return 0

    print("⚠️  This will make live API calls to Sina Finance (10 calls). Continue? [y/N] ", end="")
    if input().strip().lower() != "y":
        print("Aborted.")
        return 0

    count = 0
    for code in codes:
        print(f"  📡 {code}...", end=" ", flush=True)
        df = lynx_signal.fetch_daily_bars(code)
        if df is not None and not df.empty:
            n = cache.put_daily_ohlcv(code, df, source="sina")
            print(f"✅ {n} rows")
            count += 1
        else:
            print("❌ no data")
        time.sleep(2)
    return count


def cmd_warm(args: argparse.Namespace, db_path: str) -> None:
    cache = UnifiedCache(db_path=db_path)
    codes = _load_stock_codes()
    print(f"🔥 Cache warm: {len(codes)} stocks from stock_pool.csv\n")
    total = 0

    if args.mindlynx or args.all_:
        print("─ MindLynx DB ─")
        total += cmd_warm_mindlynx(cache, codes)

    if args.tradingagent or args.all_:
        print("\n─ TradingAgent CSVs ─")
        total += cmd_warm_tradingagent(cache, codes)

    if args.lynx or args.all_:
        print("\n─ lynx_vnpy (Sina API) ─")
        total += cmd_warm_lynx(cache, codes)

    if total == 0 and not (args.mindlynx or args.tradingagent or args.lynx or args.all_):
        print("Specify a source: --mindlynx, --tradingagent, --lynx, or --all")

    print(f"\n✅ Warmed {total} stocks total")


# ═══════════════════════════════════════
# clear
# ═══════════════════════════════════════

def cmd_clear(args: argparse.Namespace, db_path: str) -> None:
    cache = UnifiedCache(db_path=db_path)

    if args.expired:
        n = cache.clear_expired()
        print(f"🗑  Cleared {n} expired rows")
        return

    if args.all_:
        codes = _load_stock_codes()
        for code in codes:
            cache.clear_stock(code)
        print(f"🗑  Cleared all {len(codes)} stocks")
        return

    if args.code:
        cache.clear_stock(args.code)
        print(f"🗑  Cleared {args.code}")
    else:
        print("Specify: --all, --expired, or a stock code")


# ═══════════════════════════════════════
# main
# ═══════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Cache CLI",
        epilog="Examples: cache_cli.py stats | cache_cli.py warm --all | cache_cli.py clear --expired",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # stats
    p_stats = sub.add_parser("stats", help="Show cache statistics")
    p_stats.add_argument("--db", default=DEFAULT_DB_PATH, help="Cache DB path")

    # ls
    p_ls = sub.add_parser("ls", help="List cached stocks")
    p_ls.add_argument("--db", default=DEFAULT_DB_PATH, help="Cache DB path")

    # warm
    p_warm = sub.add_parser("warm", help="Pre-warm cache from subsystems")
    p_warm.add_argument("--db", default=DEFAULT_DB_PATH, help="Cache DB path")
    p_warm.add_argument("--mindlynx", action="store_true", help="Import from MindLynx DB")
    p_warm.add_argument("--tradingagent", action="store_true", help="Import from TradingAgent CSVs")
    p_warm.add_argument("--lynx", action="store_true", help="Fetch from Sina API (live)")
    p_warm.add_argument("--all", dest="all_", action="store_true", help="All sources")

    # clear
    p_clear = sub.add_parser("clear", help="Clear cached data")
    p_clear.add_argument("--db", default=DEFAULT_DB_PATH, help="Cache DB path")
    p_clear.add_argument("code", nargs="?", help="Stock code to clear")
    p_clear.add_argument("--all", dest="all_", action="store_true", help="Clear all stocks")
    p_clear.add_argument("--expired", action="store_true", help="Clear only expired rows")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.command == "stats":
        cmd_stats(args.db)
    elif args.command == "ls":
        cmd_ls(args.db)
    elif args.command == "warm":
        cmd_warm(args, args.db)
    elif args.command == "clear":
        cmd_clear(args, args.db)
