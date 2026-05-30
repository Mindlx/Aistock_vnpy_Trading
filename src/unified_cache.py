"""
Unified Data Cache Layer — Shared across three subsystems.

Design:
  - SQLite + WAL mode for concurrent reads (stdlib, zero extra deps)
  - Standard columns: ["date","open","high","low","close","volume","amount","pct_chg"]
  - Column auto-detection for Sina (Chinese col names), yfinance (English), MindLynx (standard)
  - Configurable TTL per data type (daily bars: 1d, realtime quotes: 15min, fundamentals: 7d)
  - cache_meta table tracks last-fetched timestamps per (stock_code, data_type)

Usage (zero-intrusion to subsystems):
    from src.unified_cache import UnifiedCache
    cache = UnifiedCache()
    df = cache.get_daily_ohlcv("601801")
    if df is None:
        df = fetch_from_external_api("601801")
        cache.put_daily_ohlcv("601801", df, source="sina")

Rate-limit savings projection (10 stocks):
    Before:  lynx 10 API calls + MindLynx 10 fetches + TradingAgent 10 yfinance = 30+/run
    After:   lynx 0 calls (cache hit) + others unchanged = ~10-20/run (~60% reduction for lynx path)

⚠️ 仅供学习和研究目的
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── Standard column names (matches MindLynx data_provider/base.py) ──
STANDARD_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]

# ── Column name mappings for source → standard normalization ──
# Order matters: first match wins. Checked in detection order.
COLUMN_MAPPINGS: list[dict[str, str]] = [
    # Sina Finance (lynx_vnpy): Chinese column names
    {"date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘", "volume": "成交量"},
    # yfinance (TradingAgent): English column names
    {"date": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"},
    # MindLynx efinance/akshare/tushare: already standard
    {"date": "date", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"},
]

# Reverse mappings: standard → source (for cache hits returning to subsystem)
REVERSE_MAPPINGS: dict[str, dict[str, str]] = {
    "sina": dict(COLUMN_MAPPINGS[0]),       # date→日期, open→开盘, ...
    "yfinance": dict(COLUMN_MAPPINGS[1]),   # date→Date, open→Open, ...
}


class UnifiedCache:
    """SQLite-based cache with WAL mode for concurrent reads.

    Parameters:
        db_path: Path to the SQLite database file.
        default_ttl: Default TTL in seconds for all data types (86400 = 1 day).
    """

    def __init__(
        self,
        db_path: str = "data/unified_cache/ohlcv_cache.db",
        default_ttl: int = 86400,
    ):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._default_ttl = default_ttl
        self._init_db()
        logger.info(f"UnifiedCache ready: {self._db_path} (WAL mode)")

    # ═══════════════════════════════════════
    # Internal: DB initialization & connection
    # ═══════════════════════════════════════

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA cache_size=-8000")  # 8MB page cache
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_ohlcv (
                    stock_code TEXT NOT NULL,
                    date      TEXT NOT NULL,
                    open      REAL,
                    high      REAL,
                    low       REAL,
                    close     REAL,
                    volume    REAL,
                    amount    REAL DEFAULT 0,
                    pct_chg   REAL DEFAULT 0,
                    source    TEXT DEFAULT '',
                    fetched_at REAL NOT NULL,
                    PRIMARY KEY (stock_code, date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ohlcv_code
                    ON daily_ohlcv(stock_code)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ohlcv_fetched
                    ON daily_ohlcv(fetched_at)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_meta (
                    stock_code   TEXT NOT NULL,
                    data_type    TEXT NOT NULL,
                    last_fetched REAL,
                    row_count    INTEGER DEFAULT 0,
                    source       TEXT DEFAULT '',
                    PRIMARY KEY (stock_code, data_type)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    # ═══════════════════════════════════════
    # Public API: get / put / is_fresh
    # ═══════════════════════════════════════

    def get_daily_ohlcv(
        self,
        stock_code: str,
        days: int = 120,
        ttl_seconds: Optional[int] = None,
        target_source: str = "",
        reverse_map: bool = False,
        reverse_map_source: str = "",
    ) -> Optional[pd.DataFrame]:
        """Retrieve cached OHLCV data.

        Args:
            stock_code: Stock code (e.g. "601801").
            days: Number of latest rows to return.
            ttl_seconds: Override the default TTL. None uses self._default_ttl.
            target_source: If set, only return data fetched by this source.
            reverse_map: If True, rename standard columns back to source format
                         (e.g., date→日期 for Sina). Use with reverse_map_source.
            reverse_map_source: "sina" or "yfinance".

        Returns:
            DataFrame with standard columns, or None if cache miss/expired.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        conn = self._get_conn()
        try:
            meta = conn.execute(
                "SELECT last_fetched, row_count FROM cache_meta "
                "WHERE stock_code=? AND data_type='daily_ohlcv'",
                (stock_code,),
            ).fetchone()
            if meta is None:
                return None
            last_fetched, _ = meta
            if time.time() - last_fetched > ttl:
                logger.debug(f"[Cache] {stock_code} expired (age={time.time()-last_fetched:.0f}s)")
                return None

            where = "WHERE stock_code=?"
            params: list[Any] = [stock_code]
            if target_source:
                where += " AND source=?"
                params.append(target_source)

            rows = conn.execute(
                f"SELECT date, open, high, low, close, volume, amount, pct_chg "
                f"FROM daily_ohlcv {where} "
                f"ORDER BY date DESC LIMIT ?",
                params + [days],
            ).fetchall()

            if not rows:
                return None

            df = pd.DataFrame(rows, columns=STANDARD_COLUMNS)
            df = df.sort_values("date").reset_index(drop=True)

            # Convert numeric columns
            for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            if reverse_map and reverse_map_source in REVERSE_MAPPINGS:
                rmap = REVERSE_MAPPINGS[reverse_map_source]
                df = df.rename(columns=rmap)
                logger.debug(f"[Cache] {stock_code} hit → {len(df)} rows (remapped to {reverse_map_source})")
            else:
                logger.debug(f"[Cache] {stock_code} hit → {len(df)} rows")

            return df

        finally:
            conn.close()

    def put_daily_ohlcv(
        self,
        stock_code: str,
        df: pd.DataFrame,
        source: str = "",
        target_date_col: str = "",
    ) -> int:
        """Store OHLCV data in cache. Upserts by (stock_code, date).

        Auto-detects column names from known mappings (Sina Chinese, yfinance English,
        or standard).

        Args:
            stock_code: Stock code.
            df: DataFrame with OHLCV data.
            source: Label for the data source (e.g., "sina", "yfinance", "efinance").
            target_date_col: If non-empty, use this column as the date field
                             (overrides auto-detection).

        Returns:
            Number of rows inserted.
        """
        if df is None or df.empty:
            return 0

        conn = self._get_conn()
        now = time.time()
        try:
            # Detect column mapping
            col_map = self._detect_columns(df)

            # Determine date column
            date_col = target_date_col if target_date_col else col_map.get("date", "date")

            rows = []
            for _, row in df.iterrows():
                date_val = str(row.get(date_col, ""))
                if not date_val or date_val in ("nan", "None", "NaT"):
                    continue

                rows.append((
                    stock_code,
                    date_val,
                    self._safe_float(row.get(col_map.get("open", ""))),
                    self._safe_float(row.get(col_map.get("high", ""))),
                    self._safe_float(row.get(col_map.get("low", ""))),
                    self._safe_float(row.get(col_map.get("close", ""))),
                    self._safe_float(row.get(col_map.get("volume", ""))),
                    self._safe_float(row.get(col_map.get("amount", ""))),
                    self._safe_float(row.get(col_map.get("pct_chg", ""))),
                    source,
                    now,
                ))

            if not rows:
                return 0

            conn.executemany(
                "INSERT OR REPLACE INTO daily_ohlcv "
                "(stock_code, date, open, high, low, close, volume, amount, pct_chg, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.execute(
                "INSERT OR REPLACE INTO cache_meta (stock_code, data_type, last_fetched, row_count, source) "
                "VALUES (?, 'daily_ohlcv', ?, ?, ?)",
                (stock_code, now, len(rows), source),
            )
            conn.commit()
            logger.info(f"[Cache] {stock_code} stored {len(rows)} rows (source={source})")
            return len(rows)
        finally:
            conn.close()

    def is_fresh(
        self,
        stock_code: str,
        data_type: str = "daily_ohlcv",
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """Check if cached data is within TTL."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        conn = self._get_conn()
        try:
            meta = conn.execute(
                "SELECT last_fetched FROM cache_meta WHERE stock_code=? AND data_type=?",
                (stock_code, data_type),
            ).fetchone()
            if meta is None:
                return False
            return (time.time() - meta[0]) < ttl
        finally:
            conn.close()

    # ═══════════════════════════════════════
    # Maintenance
    # ═══════════════════════════════════════

    def clear_stock(self, stock_code: str) -> None:
        """Remove all cached data for a single stock."""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM daily_ohlcv WHERE stock_code=?", (stock_code,))
            conn.execute("DELETE FROM cache_meta WHERE stock_code=?", (stock_code,))
            conn.commit()
            logger.info(f"[Cache] cleared {stock_code}")
        finally:
            conn.close()

    def clear_expired(self, ttl_seconds: Optional[int] = None) -> int:
        """Remove rows older than TTL. Returns count of deleted rows."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        cutoff = time.time() - ttl
        conn = self._get_conn()
        try:
            deleted = conn.execute(
                "DELETE FROM daily_ohlcv WHERE fetched_at < ?", (cutoff,)
            ).rowcount
            conn.execute("DELETE FROM cache_meta WHERE last_fetched < ?", (cutoff,))
            conn.commit()
            if deleted > 0:
                logger.info(f"[Cache] cleared {deleted} expired rows")
            return deleted
        finally:
            conn.close()

    def get_stats(self) -> dict[str, Any]:
        """Return cache statistics for monitoring."""
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM daily_ohlcv").fetchone()[0]
            codes = conn.execute(
                "SELECT DISTINCT stock_code FROM daily_ohlcv"
            ).fetchall()
            sources = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM daily_ohlcv "
                "GROUP BY source ORDER BY cnt DESC"
            ).fetchall()
            meta_rows = conn.execute(
                "SELECT stock_code, data_type, last_fetched, row_count, source "
                "FROM cache_meta ORDER BY last_fetched DESC"
            ).fetchall()
            return {
                "db_path": str(self._db_path),
                "total_rows": total,
                "cached_stocks": [c[0] for c in codes],
                "sources": {s: c for s, c in sources},
                "meta": [
                    {"code": m[0], "type": m[1], "age_s": int(time.time() - m[2]),
                     "rows": m[3], "source": m[4]}
                    for m in meta_rows
                ],
            }
        finally:
            conn.close()

    # ═══════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════

    @staticmethod
    def _detect_columns(df: pd.DataFrame) -> dict[str, str]:
        """Map source DataFrame columns to standard names.

        Tries each known mapping in order. Returns dict like:
            {"date": "日期", "open": "开盘", "high": "最高", ...}
        """
        for mapping in COLUMN_MAPPINGS:
            if mapping["date"] in df.columns:
                return mapping
        # Fallback: case-insensitive match
        col_map: dict[str, str] = {}
        for std_col in ["date", "open", "high", "low", "close", "volume"]:
            for col in df.columns:
                if col.lower() == std_col.lower():
                    col_map[std_col] = col
                    break
        return col_map

    @staticmethod
    def _safe_float(val: Any) -> float:
        """Convert value to float, returning 0.0 on failure."""
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0


# ── Pre-built TTL policies ──

def get_ttl_policy(data_type: str) -> int:
    """Return recommended TTL in seconds for a data type.

    These can be overridden via config/settings.yaml → unified_cache → ttl_overrides.
    """
    policies = {
        "daily_ohlcv": 86400,       # 24 hours — new daily bar available next trading day
        "daily_ohlcv_intraday": 900,  # 15 min — intraday updates during market hours
        "realtime_quote": 900,      # 15 min — realtime quotes stale quickly
        "fundamentals": 604800,     # 7 days — fundamentals change slowly
        "news": 3600,               # 1 hour — news is time-sensitive
    }
    return policies.get(data_type, 86400)


# ── Singleton convenience ──
_global_cache: Optional[UnifiedCache] = None


def get_cache(db_path: str = "data/unified_cache/ohlcv_cache.db") -> UnifiedCache:
    """Get or create the global cache instance."""
    global _global_cache
    if _global_cache is None:
        _global_cache = UnifiedCache(db_path=db_path)
    return _global_cache
