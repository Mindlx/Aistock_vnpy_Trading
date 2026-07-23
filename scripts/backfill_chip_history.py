#!/usr/bin/env python3
"""回填筹码分布完整历史序列 — 一次性写入全部历史行。"""
import logging
import os
import sqlite3
import sys
import time
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "data/data_warehouse.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    all_codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_ohlcv ORDER BY stock_code"
    ).fetchall()]
    done = set(r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM chip_distribution"
    ).fetchall())
    conn.close()

    pending = [c for c in all_codes if c not in done]
    logger.info("已有 %d 只, 待处理 %d 只", len(done), len(pending))

    from services.data_warehouse.fetchers import ChipFetcher
    from services.data_warehouse.storage import DataLake

    fetcher = ChipFetcher()
    lake = DataLake()
    ok = fail = total_rows = 0

    for code in pending:
        rows = fetcher.fetch_all(code)
        if rows:
            cnt = lake.batch_upsert_chip_distribution(code, rows)
            total_rows += cnt
            ok += 1
            if ok % 10 == 0:
                logger.info("进度: %d/%d, 共 %d 行", ok, len(pending), total_rows)
        else:
            fail += 1
        time.sleep(4 + random.uniform(0, 1))

    logger.info("完成: %d OK, %d fail, 共 %d 行历史筹码", ok, fail, total_rows)


if __name__ == "__main__":
    main()
