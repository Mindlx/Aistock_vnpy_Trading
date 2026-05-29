"""Factor expression cache for avoiding redundant computation.

Two-tier cache:
  1. In-memory LRU (O(1) access, thread-safe)
  2. SQLite disk cache (persists across restarts)

Caches factor expressions like "calc_macd('300652', 12, 26, 9)"
keyed by (function_name, stock_code, params_hash).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)


class LRUCache:
    """Thread-safe LRU cache with max size."""

    def __init__(self, max_size: int = 512):
        self._cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str, ttl_seconds: float = 0) -> object | None:
        """Get item if not expired. TTL=0 means no expiry."""
        with self._lock:
            if key not in self._cache:
                return None
            ts, value = self._cache[key]
            if ttl_seconds > 0 and time.time() - ts > ttl_seconds:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return value

    def set(self, key: str, value: object) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (time.time(), value)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


class SQLiteExpressionCache:
    """Disk-based cache for expensive expression results."""

    def __init__(self, db_path: str = "data/expression_cache.db"):
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS expr_cache ("
            "  key TEXT PRIMARY KEY,"
            "  value BLOB,"
            "  created_at REAL"
            ")"
        )
        conn.commit()
        conn.close()

    def get(self, key: str, ttl_seconds: float = 86400) -> object | None:
        try:
            conn = sqlite3.connect(self._db_path)
            row = conn.execute(
                "SELECT value, created_at FROM expr_cache WHERE key=?",
                (key,),
            ).fetchone()
            conn.close()
            if row is None:
                return None
            import pickle
            value, created_at = pickle.loads(row[0]), row[1]
            if ttl_seconds > 0 and time.time() - created_at > ttl_seconds:
                self._delete(key)
                return None
            return value
        except Exception:
            return None

    def set(self, key: str, value: object) -> None:
        try:
            import pickle
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "INSERT OR REPLACE INTO expr_cache (key, value, created_at) VALUES (?, ?, ?)",
                (key, pickle.dumps(value), time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.debug("[ExprCache] write failed: %s", exc)

    def _delete(self, key: str) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("DELETE FROM expr_cache WHERE key=?", (key,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def clear(self) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("DELETE FROM expr_cache")
            conn.commit()
            conn.close()
        except Exception:
            pass


# Global cache instances
_mem_cache = LRUCache(max_size=512)
_disk_cache = SQLiteExpressionCache()


def cache_key(func_name: str, *args) -> str:
    """Generate a deterministic cache key from function name and args."""
    raw = f"{func_name}:{args}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def cached_compute(
    func_name: str,
    stock_code: str,
    *args,
    mem_ttl: float = 300,       # 5 min memory TTL
    disk_ttl: float = 86400,    # 24 hour disk TTL
    force_refresh: bool = False,
) -> object | None:
    """Try to get a cached computation result. Returns None on cache miss.

    Usage:
        result = cached_compute("macd", "300652", 12, 26, 9)
        if result is None:
            result = compute_macd(...)
            cache_set("macd", "300652", result, 12, 26, 9)
    """
    if force_refresh:
        return None
    key = cache_key(func_name, stock_code, *args)
    val = _mem_cache.get(key, mem_ttl)
    if val is not None:
        return val
    val = _disk_cache.get(key, disk_ttl)
    if val is not None:
        _mem_cache.set(key, val)
    return val


def cache_set(func_name: str, stock_code: str, value: object, *args) -> None:
    """Store a computation result in both caches."""
    key = cache_key(func_name, stock_code, *args)
    _mem_cache.set(key, value)
    _disk_cache.set(key, value)


def clear_all_caches() -> None:
    _mem_cache.clear()
    _disk_cache.clear()
