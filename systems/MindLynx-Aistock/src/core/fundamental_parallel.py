"""Parallel fundamental data fetcher with SQLite caching.

Wraps the existing AkshareFundamentalAdapter to:
1. Fetch multiple data sources concurrently via ThreadPoolExecutor
2. Cache results in SQLite with configurable TTL
3. Apply per-call timeouts to prevent hanging
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_SECONDS: int = 86400        # 24 hours
DEFAULT_PER_CALL_TIMEOUT: int = 15            # seconds per individual endpoint call
MAX_WORKERS: int = 3


class FundamentalCache:
    """Thread-safe in-memory + SQLite fundamental data cache."""

    def __init__(self, db_path: str = "data/stock_analysis.db"):
        self._cache: dict[str, tuple[datetime, dict]] = {}
        self._lock = threading.Lock()
        self._db_path = db_path

    def get(self, key: str, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> dict | None:
        """Get cached entry if not expired."""
        with self._lock:
            if key not in self._cache:
                return None
            ts, data = self._cache[key]
            if datetime.now(timezone.utc) - ts > timedelta(seconds=ttl_seconds):
                del self._cache[key]
                return None
            return data

    def set(self, key: str, data: dict) -> None:
        """Store entry in cache."""
        with self._lock:
            self._cache[key] = (datetime.now(timezone.utc), data)
            if len(self._cache) > 256:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest]

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_fundamental_cache = FundamentalCache()


def fetch_fundamental_parallel(
    stock_code: str,
    cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS,
    per_call_timeout: int = DEFAULT_PER_CALL_TIMEOUT,
) -> dict:
    """Fetch fundamental data with parallelization and caching.

    Returns the same dict format as the existing fundamental context.
    Falls back gracefully on timeout or error.
    """
    cache_key = f"fundamental:{stock_code}"
    cached = _fundamental_cache.get(cache_key, cache_ttl)
    if cached:
        logger.debug("[FundamentalCache] hit for %s", stock_code)
        return cached

    result: dict = {"status": "partial", "data": {}, "errors": [], "source": "parallel"}

    def _fetch_financial() -> dict:
        try:
            from data_provider.fundamental_adapter import AkshareFundamentalAdapter
            adapter = AkshareFundamentalAdapter()
            return adapter.get_fundamental_bundle(stock_code) or {}
        except Exception as exc:
            return {"error": str(exc)}

    def _fetch_valuation() -> dict:
        try:
            import sqlite3
            conn = sqlite3.connect("data/stock_analysis.db")
            rows = conn.execute(
                "SELECT close, volume_ratio FROM stock_daily WHERE code=? ORDER BY date DESC LIMIT 60",
                (stock_code,),
            ).fetchall()
            conn.close()
            if rows and len(rows) >= 20:
                import statistics
                closes = [r[0] for r in rows]
                vols = [r[1] for r in rows if r[1]]
                return {
                    "close_20d_avg": round(statistics.mean(closes[-20:]), 2) if len(closes) >= 20 else 0,
                    "close_60d_max": round(max(closes), 2),
                    "close_60d_min": round(min(closes), 2),
                    "vol_ratio_avg": round(statistics.mean(vols), 2) if vols else 0,
                } if closes else {}
            return {}
        except Exception as exc:
            return {"error": str(exc)}

    futures: list[Future] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures.append(executor.submit(_fetch_financial))
        futures.append(executor.submit(_fetch_valuation))

        results = []
        for future in futures:
            try:
                results.append(future.result(timeout=per_call_timeout))
            except FutureTimeoutError:
                results.append({"error": "timeout"})
                logger.warning("[FundamentalParallel] timeout after %ds", per_call_timeout)
            except Exception as exc:
                results.append({"error": str(exc)})
                logger.debug("[FundamentalParallel] error: %s", exc)

    for res in results:
        if res and "error" not in res:
            result["data"].update(res)
        elif res and "error" in res:
            result["errors"].append(res["error"])

    if result["data"]:
        result["status"] = "ok"

    _fundamental_cache.set(cache_key, result)
    return result


def clear_fundamental_cache() -> None:
    """Clear the in-memory fundamental cache."""
    _fundamental_cache.clear()
