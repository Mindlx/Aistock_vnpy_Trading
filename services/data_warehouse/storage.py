"""SQLite 数据湖 — 全部 8 张表 + CRUD 操作

所有外部访问通过 WarehouseReader (warehouse.py), 本模块不对外暴露。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

from services.data_warehouse.config import DataWarehouseConfig

logger = logging.getLogger(__name__)

# ── 建表 DDL ──
_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA cache_size=-16000;

CREATE TABLE IF NOT EXISTS daily_ohlcv (
    stock_code  TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL, high   REAL, low    REAL,
    close       REAL, volume REAL, amount REAL DEFAULT 0.0,
    pct_chg     REAL DEFAULT 0.0,
    turnover    REAL DEFAULT 0.0,
    source      TEXT DEFAULT '',
    fetched_at  REAL NOT NULL,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_code ON daily_ohlcv(stock_code);
CREATE INDEX IF NOT EXISTS idx_ohlcv_fetched ON daily_ohlcv(fetched_at);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON daily_ohlcv(date);

CREATE TABLE IF NOT EXISTS realtime_quotes (
    stock_code   TEXT PRIMARY KEY,
    name         TEXT DEFAULT '',
    price        REAL, change_pct REAL, change_amt REAL,
    volume       REAL, amount     REAL, volume_ratio REAL,
    turnover     REAL, high REAL, low REAL, open REAL, pre_close REAL,
    fetched_at   REAL NOT NULL,
    source       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rtq_fetched ON realtime_quotes(fetched_at);

CREATE TABLE IF NOT EXISTS financial_indicators (
    stock_code      TEXT NOT NULL,
    period          TEXT NOT NULL,
    indicator_name  TEXT NOT NULL,
    value           REAL,
    unit            TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    fetched_at      REAL NOT NULL,
    PRIMARY KEY (stock_code, period, indicator_name)
);
CREATE INDEX IF NOT EXISTS idx_fin_code ON financial_indicators(stock_code);
CREATE INDEX IF NOT EXISTS idx_fin_period ON financial_indicators(period);

CREATE TABLE IF NOT EXISTS capital_flows (
    stock_code      TEXT NOT NULL,
    date            TEXT NOT NULL,
    main_net_flow   REAL DEFAULT 0.0,
    super_large_net REAL DEFAULT 0.0,
    large_net       REAL DEFAULT 0.0,
    medium_net      REAL DEFAULT 0.0,
    small_net       REAL DEFAULT 0.0,
    north_flow      REAL DEFAULT 0.0,
    north_hold_pct  REAL DEFAULT 0.0,
    source          TEXT DEFAULT '',
    fetched_at      REAL NOT NULL,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_capf_code ON capital_flows(stock_code);
CREATE INDEX IF NOT EXISTS idx_capf_date ON capital_flows(date);

CREATE TABLE IF NOT EXISTS news_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code      TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL,
    url             TEXT DEFAULT '',
    summary         TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    category        TEXT DEFAULT '',
    importance      INTEGER DEFAULT 0,
    published_at    TEXT DEFAULT '',
    created_at      REAL NOT NULL,
    ttl_seconds     INTEGER DEFAULT 86400
);
CREATE INDEX IF NOT EXISTS idx_news_code ON news_events(stock_code);
CREATE INDEX IF NOT EXISTS idx_news_created ON news_events(created_at);
CREATE INDEX IF NOT EXISTS idx_news_importance ON news_events(importance);

CREATE TABLE IF NOT EXISTS fundamentals (
    stock_code  TEXT PRIMARY KEY,
    name        TEXT DEFAULT '',
    industry    TEXT DEFAULT '',
    market_cap  REAL DEFAULT 0.0,
    pe_ttm      REAL DEFAULT 0.0,
    pb          REAL DEFAULT 0.0,
    roe         REAL DEFAULT 0.0,
    revenue_yoy REAL DEFAULT 0.0,
    profit_yoy  REAL DEFAULT 0.0,
    debt_ratio  REAL DEFAULT 0.0,
    dividend_yield REAL DEFAULT 0.0,
    fetched_at  REAL NOT NULL,
    source      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chip_distribution (
    stock_code  TEXT NOT NULL,
    date        TEXT NOT NULL,
    profit_ratio REAL DEFAULT 0.0,
    avg_cost    REAL DEFAULT 0.0,
    concentration REAL DEFAULT 0.0,
    source      TEXT DEFAULT '',
    fetched_at  REAL NOT NULL,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_chip_code ON chip_distribution(stock_code);

CREATE TABLE IF NOT EXISTS global_ohlcv (
    market      TEXT NOT NULL,
    code        TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL, high   REAL, low    REAL,
    close       REAL, volume REAL, amount REAL DEFAULT 0.0,
    pct_chg     REAL DEFAULT 0.0,
    source      TEXT DEFAULT '',
    fetched_at  REAL NOT NULL,
    PRIMARY KEY (market, code, date)
);
CREATE INDEX IF NOT EXISTS idx_gl_code ON global_ohlcv(market, code);
CREATE INDEX IF NOT EXISTS idx_gl_date ON global_ohlcv(date);

CREATE TABLE IF NOT EXISTS global_fundamentals (
    market      TEXT NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT DEFAULT '',
    sector      TEXT DEFAULT '',
    industry   TEXT DEFAULT '',
    pe_ttm      REAL DEFAULT 0.0,
    pb          REAL DEFAULT 0.0,
    market_cap  REAL DEFAULT 0.0,
    dividend_yield REAL DEFAULT 0.0,
    beta        REAL DEFAULT 0.0,
    source      TEXT DEFAULT '',
    fetched_at  REAL NOT NULL,
    PRIMARY KEY (market, code)
);

CREATE TABLE IF NOT EXISTS index_ohlcv (
    index_code  TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL, high   REAL, low    REAL,
    close       REAL, volume REAL, amount REAL DEFAULT 0.0,
    pct_chg     REAL DEFAULT 0.0,
    source      TEXT DEFAULT '',
    fetched_at  REAL NOT NULL,
    PRIMARY KEY (index_code, date)
);
CREATE INDEX IF NOT EXISTS idx_idx_code ON index_ohlcv(index_code);
CREATE INDEX IF NOT EXISTS idx_idx_date ON index_ohlcv(date);

CREATE TABLE IF NOT EXISTS cache_meta (
    stock_code   TEXT NOT NULL,
    data_type    TEXT NOT NULL,
    last_fetched REAL,
    row_count    INTEGER DEFAULT 0,
    source       TEXT DEFAULT '',
    ttl_seconds  INTEGER DEFAULT 86400,
    PRIMARY KEY (stock_code, data_type)
);
CREATE INDEX IF NOT EXISTS idx_meta_type ON cache_meta(data_type, last_fetched);
"""


class DataLake:
    """SQLite 数据湖封装 — 线程安全的 WAL 模式读写。"""

    def __init__(self, db_path: str | None = None):
        cfg = DataWarehouseConfig.get_instance()
        self._db_path = db_path or cfg.db_path
        self._local = threading.local()
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        for stmt in _SCHEMA_SQL.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()

    # ── 内部: 缓存元数据追踪 ──

    def _touch_meta(self, code: str, data_type: str, row_count: int,
                    source: str, ttl: int) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO cache_meta
               (stock_code, data_type, last_fetched, row_count, source, ttl_seconds)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (code, data_type, time.time(), row_count, source, ttl),
        )
        conn.commit()

    def get_cache_meta(self, code: str, data_type: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM cache_meta WHERE stock_code=? AND data_type=?",
            (code, data_type),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ── daily_ohlcv ──

    def upsert_ohlcv(self, code: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        conn = self._get_conn()
        count = 0
        for r in rows:
            conn.execute(
                """INSERT OR REPLACE INTO daily_ohlcv
                   (stock_code, date, open, high, low, close, volume, amount,
                    pct_chg, turnover, source, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code, r["date"], r.get("open"), r.get("high"), r.get("low"),
                 r.get("close"), r.get("volume"), r.get("amount", 0.0),
                 r.get("pct_chg", 0.0), r.get("turnover", 0.0),
                 r.get("source", "warehouse"), time.time()),
            )
            count += 1
        conn.commit()
        self._touch_meta(code, "daily_ohlcv", count,
                         "warehouse", 86400)
        return count

    def update_ohlcv_turnover(self, code: str, date_turnover_pairs: list[tuple[str, float]]) -> int:
        """回填换手率: 只更新 turnover 字段, 不修改其他 OHLCV 数据。

        Args:
            code: 股票代码
            date_turnover_pairs: [(date_str, turnover_pct), ...]

        Returns:
            更新的行数
        """
        if not date_turnover_pairs:
            return 0
        conn = self._get_conn()
        count = 0
        for date_str, turnover_val in date_turnover_pairs:
            conn.execute(
                "UPDATE daily_ohlcv SET turnover=?, fetched_at=? WHERE stock_code=? AND date=?",
                (turnover_val, time.time(), code, date_str),
            )
            count += 1
        conn.commit()
        return count

    def query_ohlcv(self, code: str, start: str = "", end: str = "",
                    days: int = 120) -> list[dict]:
        conn = self._get_conn()
        if start and end:
            rows = conn.execute(
                "SELECT * FROM daily_ohlcv WHERE stock_code=? AND date>=? AND date<=? ORDER BY date",
                (code, start, end),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM daily_ohlcv WHERE stock_code=? ORDER BY date DESC LIMIT ?",
                (code, days),
            ).fetchall()
        return [dict(r) for r in rows][::-1]

    # ── realtime_quotes ──

    def upsert_realtime(self, code: str, data: dict) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO realtime_quotes
               (stock_code, name, price, change_pct, change_amt, volume,
                amount, volume_ratio, turnover, high, low, open, pre_close,
                fetched_at, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, data.get("name", ""), data.get("price"),
             data.get("change_pct"), data.get("change_amt"),
             data.get("volume"), data.get("amount"),
             data.get("volume_ratio"), data.get("turnover"),
             data.get("high"), data.get("low"), data.get("open"),
             data.get("pre_close"), time.time(), data.get("source", "warehouse")),
        )
        conn.commit()
        self._touch_meta(code, "realtime_quotes", 1, "warehouse", 300)

    def query_realtime(self, code: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM realtime_quotes WHERE stock_code=?", (code,)
        ).fetchone()
        return dict(row) if row else None

    def query_realtime_batch(self, codes: list[str]) -> dict[str, dict]:
        if not codes:
            return {}
        placeholders = ",".join("?" for _ in codes)
        conn = self._get_conn()
        rows = conn.execute(
            f"SELECT * FROM realtime_quotes WHERE stock_code IN ({placeholders})",
            codes,
        ).fetchall()
        return {r["stock_code"]: dict(r) for r in rows}

    # ── financial_indicators ──

    def upsert_financial(self, code: str, period: str,
                         indicators: dict[str, float]) -> int:
        conn = self._get_conn()
        count = 0
        now = time.time()
        for name, value in indicators.items():
            if value is None:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO financial_indicators
                   (stock_code, period, indicator_name, value, fetched_at)
                   VALUES (?,?,?,?,?)""",
                (code, period, name, float(value), now),
            )
            count += 1
        conn.commit()
        self._touch_meta(code, "financial_indicators", count, "warehouse", 86400)
        return count

    def query_financial(self, code: str) -> dict[str, dict[str, float]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT indicator_name, period, value FROM financial_indicators "
            "WHERE stock_code=? ORDER BY period DESC", (code,)
        ).fetchall()
        result: dict[str, dict[str, float]] = {}
        for r in rows:
            name = r["indicator_name"]
            period = r["period"]
            val = r["value"]
            if name not in result:
                result[name] = {}
            result[name][period] = val
        return result

    # ── capital_flows ──

    def upsert_capital_flows(self, code: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        conn = self._get_conn()
        count = 0
        now = time.time()
        for r in rows:
            conn.execute(
                """INSERT OR REPLACE INTO capital_flows
                   (stock_code, date, main_net_flow, super_large_net, large_net,
                    medium_net, small_net, north_flow, north_hold_pct, source, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (code, r["date"], r.get("main_net_flow"), r.get("super_large_net"),
                 r.get("large_net"), r.get("medium_net"), r.get("small_net"),
                 r.get("north_flow"), r.get("north_hold_pct"),
                 r.get("source", "warehouse"), now),
            )
            count += 1
        conn.commit()
        self._touch_meta(code, "capital_flows", count, "warehouse", 86400)
        return count

    def query_capital_flows(self, code: str, days: int = 30) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM capital_flows WHERE stock_code=? "
            "ORDER BY date DESC LIMIT ?", (code, days)
        ).fetchall()
        return [dict(r) for r in rows][::-1]

    # ── news_events ──

    def insert_news(self, items: list[dict]) -> int:
        if not items:
            return 0
        conn = self._get_conn()
        count = 0
        now = time.time()
        for item in items:
            try:
                conn.execute(
                    """INSERT INTO news_events
                       (stock_code, title, url, summary, source, category,
                        importance, published_at, created_at, ttl_seconds)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (item.get("stock_code", ""), item["title"],
                     item.get("url", ""), item.get("summary", "")[:200],
                     item.get("source", ""), item.get("category", ""),
                     item.get("importance", 0), item.get("published_at", ""),
                     now, item.get("ttl_seconds", 86400)),
                )
                count += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        self._touch_meta("__global__", "news_events", count, "warehouse", 3600)
        return count

    def query_news(self, code: str = "", days: int = 7,
                   min_importance: int = 0, limit: int = 50) -> list[dict]:
        conn = self._get_conn()
        cutoff = time.time() - days * 86400
        if code:
            rows = conn.execute(
                "SELECT * FROM news_events WHERE stock_code=? AND created_at>=? "
                "AND importance>=? ORDER BY importance DESC, created_at DESC LIMIT ?",
                (code, cutoff, min_importance, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM news_events WHERE created_at>=? "
                "AND importance>=? ORDER BY importance DESC, created_at DESC LIMIT ?",
                (cutoff, min_importance, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── fundamentals ──

    def upsert_fundamentals(self, code: str, data: dict) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO fundamentals
               (stock_code, name, industry, market_cap, pe_ttm, pb, roe,
                revenue_yoy, profit_yoy, debt_ratio, dividend_yield,
                fetched_at, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, data.get("name", ""), data.get("industry", ""),
             data.get("market_cap"), data.get("pe_ttm"), data.get("pb"),
             data.get("roe"), data.get("revenue_yoy"), data.get("profit_yoy"),
             data.get("debt_ratio"), data.get("dividend_yield"),
             time.time(), data.get("source", "warehouse")),
        )
        conn.commit()
        self._touch_meta(code, "fundamentals", 1, "warehouse", 604800)

    def query_fundamentals(self, code: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM fundamentals WHERE stock_code=?", (code,)
        ).fetchone()
        return dict(row) if row else None

    # ── 统计 ──

    def stats(self) -> dict[str, Any]:
        conn = sqlite3.connect(self._db_path, timeout=5)
        try:
            tables = [
                "daily_ohlcv", "realtime_quotes", "financial_indicators",
                "capital_flows", "news_events", "fundamentals", "cache_meta",
            ]
            result = {}
            for t in tables:
                row = conn.execute(
                    f"SELECT COUNT(*) as cnt FROM {t}"
                ).fetchone()
                result[t] = row[0] if row else 0
            result["db_size_mb"] = round(
                os.path.getsize(self._db_path) / (1024 * 1024), 2
            ) if os.path.exists(self._db_path) else 0
            return result
        finally:
            conn.close()

    # ── chip_distribution ──

    def upsert_chip_distribution(self, code: str, date: str, data: dict) -> None:
        conn = self._get_conn()
        conn.execute("""INSERT OR REPLACE INTO chip_distribution
            (stock_code, date, profit_ratio, avg_cost, concentration, source, fetched_at)
            VALUES (?,?,?,?,?,?,?)""", (
            code, date,
            data.get("profit_ratio", 0), data.get("avg_cost", 0),
            data.get("concentration", 0), data.get("source", "akshare"), time.time(),
        ))
        conn.commit()
        self._touch_meta(code, "chip_distribution", 1, data.get("source", "akshare"), 86400)

    def batch_upsert_chip_distribution(self, code: str, rows: list[dict]) -> int:
        """批量写入筹码分布历史序列. rows from ChipFetcher.fetch_all()."""
        if not rows:
            return 0
        conn = self._get_conn()
        count = 0
        for r in rows:
            conn.execute("""INSERT OR REPLACE INTO chip_distribution
                (stock_code, date, profit_ratio, avg_cost, concentration, source, fetched_at)
                VALUES (?,?,?,?,?,?,?)""", (
                code, r.get("date", ""),
                r.get("profit_ratio", 0), r.get("avg_cost", 0),
                r.get("concentration", 0), r.get("source", "akshare"), time.time(),
            ))
            count += 1
        conn.commit()
        self._touch_meta(code, "chip_distribution", count, "akshare", 86400)
        return count

    def query_chip_distribution(self, code: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM chip_distribution WHERE stock_code=? ORDER BY date DESC LIMIT 1",
            (code,),
        ).fetchone()
        return dict(row) if row else None

    def query_chip_distribution_history(self, code: str) -> list[dict]:
        """查询筹码分布完整历史序列."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM chip_distribution WHERE stock_code=? ORDER BY date ASC",
            (code,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── index_ohlcv ──

    def upsert_index_ohlcv(self, index_code: str, rows: list[dict]) -> int:
        conn = self._get_conn()
        inserted = 0
        try:
            for row in rows:
                conn.execute("""INSERT OR REPLACE INTO index_ohlcv
                    (index_code, date, open, high, low, close, volume, amount, pct_chg, source, fetched_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                    index_code, row.get("date", ""),
                    row.get("open", 0), row.get("high", 0), row.get("low", 0),
                    row.get("close", 0), row.get("volume", 0), row.get("amount", 0),
                    row.get("pct_chg", 0), row.get("source", "akshare"), time.time(),
                ))
                inserted += 1
            conn.commit()
            self._touch_meta(index_code, "index_ohlcv", inserted, "akshare", 86400)
        finally:
            conn.close()
        return inserted

    def query_index_ohlcv(self, index_code: str, days: int = 60) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM index_ohlcv WHERE index_code=? ORDER BY date DESC LIMIT ?",
                (index_code, days),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── global_ohlcv (美股/港股) ──

    def upsert_global_ohlcv(self, market: str, code: str, rows: list[dict]) -> int:
        conn = self._get_conn()
        inserted = 0
        try:
            for row in rows:
                conn.execute("""INSERT OR REPLACE INTO global_ohlcv
                    (market, code, date, open, high, low, close, volume, amount, pct_chg, source, fetched_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    market, code, row.get("date", ""),
                    row.get("open", 0), row.get("high", 0), row.get("low", 0),
                    row.get("close", 0), row.get("volume", 0), row.get("amount", 0),
                    row.get("pct_chg", 0), row.get("source", "yfinance"), time.time(),
                ))
                inserted += 1
            conn.commit()
            self._touch_meta(f"{market}:{code}", "global_ohlcv", inserted, "yfinance", 86400)
        finally:
            conn.close()
        return inserted

    def query_global_ohlcv(self, market: str, code: str, days: int = 60) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM global_ohlcv WHERE market=? AND code=? ORDER BY date DESC LIMIT ?",
                (market, code, days),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── global_fundamentals (美股/港股基本面) ──

    def upsert_global_fundamentals(self, market: str, code: str, data: dict) -> None:
        conn = self._get_conn()
        try:
            conn.execute("""INSERT OR REPLACE INTO global_fundamentals
                (market, code, name, sector, industry, pe_ttm, pb, market_cap, dividend_yield, beta, source, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                market, code,
                data.get("name", ""), data.get("sector", ""), data.get("industry", ""),
                data.get("pe_ttm", 0), data.get("pb", 0), data.get("market_cap", 0),
                data.get("dividend_yield", 0), data.get("beta", 0),
                data.get("source", "yfinance"), time.time(),
            ))
            conn.commit()
            self._touch_meta(f"{market}:{code}", "global_fundamentals", 1, "yfinance", 86400)
        finally:
            conn.close()

    def query_global_fundamentals(self, market: str, code: str) -> dict | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM global_fundamentals WHERE market=? AND code=?",
                (market, code),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ── 过期清理 ──

    def purge_expired(self) -> dict[str, int]:
        """删除过期数据, 返回 {table: deleted_rows}"""
        conn = self._get_conn()
        now = time.time()
        results: dict[str, int] = {}

        # news_events: 按 ttl_seconds 过期
        deleted = conn.execute(
            "DELETE FROM news_events WHERE created_at < ? - ttl_seconds",
            (now,),
        ).rowcount
        results["news_events"] = deleted

        # 基于 cache_meta 过期其他表
        meta_rows = conn.execute(
            "SELECT * FROM cache_meta WHERE last_fetched < ? - ttl_seconds",
            (now,),
        ).fetchall()

        table_map = {
            "daily_ohlcv": "daily_ohlcv",
            "realtime_quotes": "realtime_quotes",
            "financial_indicators": "financial_indicators",
            "capital_flows": "capital_flows",
            "fundamentals": "fundamentals",
        }
        for meta in meta_rows:
            tbl = table_map.get(meta["data_type"])
            if tbl and meta["stock_code"] != "__global__":
                cnt = conn.execute(
                    f"DELETE FROM {tbl} WHERE stock_code=? AND fetched_at < ?",
                    (meta["stock_code"], now - meta["ttl_seconds"]),
                ).rowcount
                results[f"{tbl}:{meta['stock_code']}"] = cnt
                # 重置 meta
                conn.execute(
                    "DELETE FROM cache_meta WHERE stock_code=? AND data_type=?",
                    (meta["stock_code"], meta["data_type"]),
                )

        conn.commit()
        return results
