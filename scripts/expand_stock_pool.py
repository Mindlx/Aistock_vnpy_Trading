#!/usr/bin/env python3
"""
扩展数据仓库股票池 — 拉取更多股票的 OHLCV + 筹码分布

用法:
    python scripts/expand_stock_pool.py                          # 默认取市值前200
    python scripts/expand_stock_pool.py --top-n 500              # 取前500
    python scripts/expand_stock_pool.py --codes 000001,600000    # 指定股票

数据源:
    - OHLCV: Tushare Pro daily API (付费, 50/min)
    - 筹码分布: akshare EM (免费, 限流)
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "data_warehouse.db")
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")


# ── Tushare HTTP 客户端 ──


def _ts_post(api_name: str, params: dict) -> list[list] | None:
    """调用 Tushare Pro HTTP API, 返回 items 列表."""
    if not TUSHARE_TOKEN:
        logger.error("TUSHARE_TOKEN 未设置")
        return None
    import requests
    try:
        resp = requests.post(
            "http://api.tushare.pro",
            json={"api_name": api_name, "token": TUSHARE_TOKEN, "params": params},
            timeout=30,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("Tushare error: %s", data)
            return None
        d = data.get("data", {})
        return d.get("items") if d else None
    except Exception as exc:
        logger.warning("Tushare request failed: %s", exc)
        return None


# ── 股票列表获取 ──


def get_top_stocks(n: int = 200) -> list[tuple[str, str]]:
    """获取股票列表. 返回 [(ts_code, name), ...]"""
    items = _ts_post("stock_basic", {
        "exchange": "", "list_status": "L",
        "fields": "ts_code,name",
    })
    if not items:
        logger.error("无法获取股票列表")
        return []

    stocks = [(str(item[0]), str(item[1] or "")) for item in items if item[0]]
    # 剔除北交所(8开头) 和 科创板(688开头) 部分低流动性的
    stocks = [(t, n) for t, n in stocks if not t.startswith(("8",))]
    result = stocks[:n]
    logger.info("取前 %d 只: %s ~ %s", n, result[0][1], result[-1][1])
    return result


# ── 数据写入 ──


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def upsert_ohlcv(code: str, rows: list[dict]) -> int:
    """写入 OHLCV 数据到 daily_ohlcv 表."""
    if not rows:
        return 0
    conn = _get_conn()
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
             r.get("source", "tushare"), time.time()),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def upsert_chip(code: str, data: dict) -> bool:
    """写入筹码分布快照."""
    conn = _get_conn()
    try:
        today = datetime.now().strftime("%Y%m%d")
        conn.execute(
            """INSERT OR REPLACE INTO chip_distribution
               (stock_code, date, profit_ratio, avg_cost, concentration, source, fetched_at)
               VALUES (?,?,?,?,?,?,?)""",
            (code, today, data.get("profit_ratio", 0), data.get("avg_cost", 0),
             data.get("concentration", 0), data.get("source", "akshare"), time.time()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ── 数据获取 ──


def fetch_ohlcv_tushare(ts_code: str, days: int = 365) -> list[dict] | None:
    """从 Tushare 获取日K线."""
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    items = _ts_post("daily", {
        "ts_code": ts_code, "start_date": start, "end_date": end,
    })
    if not items:
        return None

    rows = []
    for item in items:
        if len(item) < 11:
            continue
        rows.append({
            "date": str(item[1]),
            "open": float(item[2] or 0),
            "high": float(item[3] or 0),
            "low": float(item[4] or 0),
            "close": float(item[5] or 0),
            "volume": float(item[9] or 0),
            "amount": float(item[10] or 0),
            "pct_chg": float(item[8] or 0),
            "turnover": 0.0,
            "source": "tushare",
        })
    return rows


def fetch_turnover_tushare(ts_code: str, days: int = 365) -> list[tuple[str, float]]:
    """从 Tushare daily_basic 获取换手率."""
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    items = _ts_post("daily_basic", {
        "ts_code": ts_code, "start_date": start, "end_date": end,
        "fields": "trade_date,turnover_rate",
    })
    if not items:
        return []
    pairs = []
    # daily_basic 列顺序: ts_code(0), trade_date(1), close(2), turnover_rate(3), ...
    for item in items:
        if len(item) < 4:
            continue
        d = str(item[1])[:8]
        tr = float(item[3]) if item[3] is not None else 0
        if d and tr > 0:
            pairs.append((d, tr))
    return pairs


def fetch_chip_distribution(code: str) -> dict | None:
    """从 akshare EM 获取筹码分布."""
    try:
        import akshare as ak
        df = ak.stock_cyq_em(symbol=code, adjust="")
        if df is not None and not df.empty:
            last = df.iloc[-1]
            return {
                "profit_ratio": float(last.get("获利比例", 0) or 0),
                "avg_cost": float(last.get("平均成本", 0) or 0),
                "concentration": float(last.get("90集中度", 0) or 0),
                "source": "akshare",
            }
    except Exception as exc:
        logger.debug("chip fetch failed %s: %s", code, exc)
    return None


# ── 主流程 ──


def expand(
    stocks: list[tuple[str, str]],
    skip_ohlcv: bool = False,
    skip_chip: bool = False,
    skip_turnover: bool = False,
) -> None:
    """对股票列表执行数据扩展."""
    # Tushare uses ts_code (600372.SH), akshare uses plain code (600372)
    ts_to_plain = lambda t: t.split(".")[0]

    # ── Step 1: OHLCV ──
    if not skip_ohlcv:
        logger.info("===== Step 1: OHLCV (%d stocks) =====", len(stocks))
        existing = set()
        conn = _get_conn()
        try:
            for r in conn.execute("SELECT DISTINCT stock_code FROM daily_ohlcv"):
                existing.add(r[0])
        finally:
            conn.close()
        logger.info("已有 %d 只, 需补充 %d 只", len(existing), len(stocks) - len(existing))

        fetched_ohlcv = 0
        for i, (ts_code, name) in enumerate(stocks):
            code = ts_to_plain(ts_code)
            if code in existing:
                continue
            rows = fetch_ohlcv_tushare(ts_code)
            if rows:
                cnt = upsert_ohlcv(code, rows)
                fetched_ohlcv += 1
                if fetched_ohlcv % 20 == 0:
                    logger.info("  OHLCV 进度: %d/%d 只", fetched_ohlcv, len(stocks) - len(existing))
            time.sleep(1.2 + random.uniform(0, 0.5))  # Tushare 50/min ≈ 1.2s间隔

        logger.info("Step 1 完成: 新增 %d 只 OHLCV 数据", fetched_ohlcv)

    # ── Step 2: 换手率回填 ──
    if not skip_turnover:
        logger.info("===== Step 2: 换手率回填 (%d stocks) =====", len(stocks))
        updated_turnover = 0
        for i, (ts_code, name) in enumerate(stocks):
            code = ts_to_plain(ts_code)
            pairs = fetch_turnover_tushare(ts_code)
            if pairs:
                conn = _get_conn()
                try:
                    for d, tr in pairs:
                        conn.execute(
                            "UPDATE daily_ohlcv SET turnover=?, fetched_at=? WHERE stock_code=? AND date=?",
                            (tr, time.time(), code, d),
                        )
                    conn.commit()
                    updated_turnover += 1
                finally:
                    conn.close()
            if i % 50 == 0:
                logger.info("  换手率进度: %d/%d", i, len(stocks))
            time.sleep(1.2 + random.uniform(0, 0.5))

        logger.info("Step 2 完成: %d 只股票换手率已回填", updated_turnover)

    # ── Step 3: 筹码分布 ──
    if not skip_chip:
        logger.info("===== Step 3: 筹码分布 (%d stocks) =====", len(stocks))
        chip_ok = 0
        chip_fail = 0
        for i, (ts_code, name) in enumerate(stocks):
            code = ts_to_plain(ts_code)
            data = fetch_chip_distribution(code)
            if data:
                upsert_chip(code, data)
                chip_ok += 1
            else:
                chip_fail += 1
            if (i + 1) % 20 == 0:
                logger.info("  筹码进度: %d/%d (成功 %d, 失败 %d)", i + 1, len(stocks), chip_ok, chip_fail)
            time.sleep(3 + random.uniform(0, 1))  # akshare EM 15/min ≈ 4s间隔

        logger.info("Step 3 完成: 成功 %d, 失败 %d", chip_ok, chip_fail)

    # ── 统计 ──
    conn = _get_conn()
    try:
        ohlcv_cnt = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM daily_ohlcv").fetchone()[0]
        ohlcv_rows = conn.execute("SELECT COUNT(*) FROM daily_ohlcv").fetchone()[0]
        turnover_rows = conn.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE turnover > 0").fetchone()[0]
        chip_cnt = conn.execute("SELECT COUNT(*) FROM chip_distribution").fetchone()[0]
        logger.info("")
        logger.info("========== 扩展完成 ==========")
        logger.info("  股票数: %d", ohlcv_cnt)
        logger.info("  OHLCV 总行数: %d", ohlcv_rows)
        logger.info("  含换手率行数: %d", turnover_rows)
        logger.info("  筹码分布记录: %d", chip_cnt)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="扩展数据仓库股票池")
    parser.add_argument("--top-n", type=int, default=200, help="拉取市值前N只 (默认200)")
    parser.add_argument("--codes", type=str, help="指定股票代码逗号分隔, 如 000001,600000")
    parser.add_argument("--skip-ohlcv", action="store_true", help="跳过OHLCV获取")
    parser.add_argument("--skip-chip", action="store_true", help="跳过筹码分布获取")
    parser.add_argument("--skip-turnover", action="store_true", help="跳过换手率回填")
    args = parser.parse_args()

    if args.codes:
        raw = [c.strip() for c in args.codes.split(",") if c.strip()]
        stocks = []
        for c in raw:
            prefix = f"{c}.SH" if c.startswith(("6", "5", "9")) else f"{c}.SZ"
            stocks.append((prefix, c))
        logger.info("自定义股票 %d 只: %s", len(stocks), args.codes)
    else:
        stocks = get_top_stocks(args.top_n)
        if not stocks:
            return
        logger.info("Top %d 股票: %s ~ %s", args.top_n, stocks[0], stocks[-1])

    expand(stocks, skip_ohlcv=args.skip_ohlcv, skip_chip=args.skip_chip, skip_turnover=args.skip_turnover)


if __name__ == "__main__":
    main()
