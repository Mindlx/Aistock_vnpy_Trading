"""跨进程令牌桶限流器 — SQLite 原子 CAS 实现

核心设计:
  - 每个 API 源 (`eastmoney` / `sina` / `tencent` / `cninfo` / `tushare`) 独立桶
  - 使用 SQLite IMMEDIATE 事务 + 条件 UPDATE 保证跨进程原子性
  - 指数退避 (1s→2s→4s→8s) + 随机抖动 ±30%
  - `@limiter.retry("source")` 装饰器一键保护任意函数

用法:
    limiter = TokenBucketLimiter()
    @limiter.retry("eastmoney", max_retries=3)
    def fetch_something(code):
        return ak.stock_zh_a_hist(...)
"""
from __future__ import annotations

import functools
import logging
import os
import random
import sqlite3
import threading
import time
from typing import Any, Callable

from services.data_warehouse.config import DataWarehouseConfig

logger = logging.getLogger(__name__)

# 东财请求计数器 (Oracle验证: 监控反爬压力)
_em_request_count = 0
_em_success_count = 0
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
]

# 桶初始化 SQL（幂等 INSERT）
_INIT_SOURCES_SQL = """
INSERT OR IGNORE INTO rate_limit_state (source, tokens, last_refill, max_tokens, refill_rate)
VALUES (?, ?, ?, ?, ?)
"""


class RateLimitError(Exception):
    """令牌耗尽 / 超时未获取到令牌"""


class TokenBucketLimiter:
    """基于 SQLite 的跨进程令牌桶限流器。"""

    SOURCE_CONFIGS: dict[str, dict[str, float]] = {}  # lazy-populated

    def __init__(self, db_path: str | None = None):
        cfg = DataWarehouseConfig.get_instance()
        self._db_path = db_path or cfg.db_path
        self._local = threading.local()
        self._ensure_schema()
        self._init_sources()

    # ── 连接管理 (每个线程独立 conn) ──

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    # ── 初始化 ──

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_state (
                source      TEXT PRIMARY KEY,
                tokens      REAL NOT NULL,
                last_refill REAL NOT NULL,
                max_tokens  REAL NOT NULL,
                refill_rate REAL NOT NULL
            )
        """)
        conn.commit()

    def _init_sources(self) -> None:
        cfg = DataWarehouseConfig.get_instance()
        conn = self._get_conn()
        now = time.time()
        for source, rl_cfg in cfg.rate_limits.items():
            conn.execute(
                _INIT_SOURCES_SQL,
                (source, rl_cfg.max_tokens, now, rl_cfg.max_tokens, rl_cfg.refill_rate),
            )
        conn.commit()

    # ── 核心: 消费令牌 ──

    def consume(
        self, source: str, tokens: float = 1.0, timeout: float = 15.0
    ) -> bool:
        """尝试消费令牌, 阻塞直到成功或超时。返回是否获取到。"""
        cfg = DataWarehouseConfig.get_instance()
        rl_cfg = cfg.rate_limits.get(source)
        if rl_cfg is None:
            return True  # 未配置的源不限制

        deadline = time.time() + timeout
        _wait_total = 0.0

        while time.time() < deadline:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT tokens, last_refill, max_tokens, refill_rate "
                    "FROM rate_limit_state WHERE source=?",
                    (source,),
                ).fetchone()

                if row is None:
                    conn.execute("ROLLBACK")
                    self._init_sources()
                    continue

                current, last_refill, max_t, rate = row
                now = time.time()

                new_tokens = min(max_t, current + (now - last_refill) * rate)

                if new_tokens >= tokens:
                    cursor = conn.execute(
                        "UPDATE rate_limit_state SET tokens=?, last_refill=? "
                        "WHERE source=? AND tokens=?",
                        (new_tokens - tokens, now, source, current),
                    )
                    conn.execute("COMMIT")
                    if cursor.rowcount > 0:
                        return True
                    # 条件 UPDATE 失败 → 其他进程已修改 → 重试
                    continue
                else:
                    conn.execute("ROLLBACK")
                    need = tokens - new_tokens
                    sleep_time = min(need / rate, deadline - time.time())
                    if sleep_time <= 0:
                        return False
                    actual = min(sleep_time, 0.05)  # 最大 50ms 轮询粒度
                    time.sleep(actual)
                    _wait_total += actual
                    continue
            except sqlite3.OperationalError:
                conn.execute("ROLLBACK")
                time.sleep(0.01)
                continue

        return False

    # ── 指数退避重试装饰器 ──

    def retry(
        self,
        source: str,
        max_retries: int = 3,
        base_delay: float | None = None,
        jitter: float | None = None,
    ) -> Callable[[Callable], Callable]:
        """装饰器: 令牌桶限流 + 指数退避重试。

        Args:
            source: API 源名称 (eastmoney/sina/tencent/...)
            max_retries: 最大重试次数 (默认使用配置值)
            base_delay: 基础延迟秒数 (默认使用配置值)
            jitter: 抖动比例 0-1 (默认使用配置值)
        """
        cfg = DataWarehouseConfig.get_instance()
        rl_cfg = cfg.rate_limits.get(source)
        if rl_cfg is None:
            raise ValueError(f"Unknown rate limit source: {source}")

        _max_retries = max_retries
        _base_delay = base_delay if base_delay is not None else rl_cfg.base_delay
        _jitter = jitter if jitter is not None else rl_cfg.jitter

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: Exception | None = None
                for attempt in range(_max_retries + 1):
                    try:
                        if not self.consume(source, timeout=15):
                            raise RateLimitError(
                                f"[{source}] 令牌耗尽，等待超时"
                            )
                        if source == "eastmoney":
                            global _em_request_count, _em_success_count
                            _em_request_count += 1
                            _em_success_count += 1
                            if _em_request_count % 10 == 0:
                                logger.info(
                                    "[EM counter] %d 请求, %d 成功, 当前源: pytdx优先",
                                    _em_request_count, _em_success_count,
                                )
                        return func(*args, **kwargs)
                    except RateLimitError:
                        raise  # 令牌耗尽直接抛，不重试
                    except Exception as exc:
                        if source == "eastmoney":
                            _em_request_count += 1
                            if _em_request_count % 10 == 0:
                                logger.info(
                                    "[EM counter] %d 请求, 累计失败, 当前源: pytdx优先",
                                    _em_request_count,
                                )
                        last_exc = exc
                        if attempt < _max_retries:
                            delay = _base_delay * (2 ** attempt)
                            jittered = delay * (1 + random.uniform(-_jitter, _jitter))
                            logger.debug(
                                "[limiter] %s 重试 %d/%d, wait=%.2fs: %s",
                                source, attempt + 1, _max_retries, jittered, exc,
                            )
                            time.sleep(max(0.1, jittered))
                raise last_exc  # type: ignore[misc]

            return wrapper

        return decorator

    # ── 工具方法 ──

    def stats(self) -> dict[str, dict]:
        """返回各源的令牌桶状态"""
        conn = sqlite3.connect(self._db_path, timeout=5)
        try:
            rows = conn.execute(
                "SELECT source, tokens, last_refill, max_tokens, refill_rate "
                "FROM rate_limit_state"
            ).fetchall()
            now = time.time()
            result = {}
            for source, tokens, last_refill, max_t, rate in rows:
                result[source] = {
                    "tokens": tokens,
                    "max_tokens": max_t,
                    "refill_rate": rate,
                    "last_refill_ago": now - last_refill,
                    "est_available": min(max_t, tokens + (now - last_refill) * rate),
                }
            return result
        finally:
            conn.close()

    def reset(self, source: str | None = None) -> None:
        """重置指定源的令牌桶（或全部）"""
        conn = sqlite3.connect(self._db_path, timeout=5)
        try:
            if source:
                conn.execute(
                    "UPDATE rate_limit_state SET tokens=max_tokens, last_refill=? WHERE source=?",
                    (time.time(), source),
                )
            else:
                conn.execute(
                    "UPDATE rate_limit_state SET tokens=max_tokens, last_refill=?",
                    (time.time(),),
                )
            conn.commit()
        finally:
            conn.close()
