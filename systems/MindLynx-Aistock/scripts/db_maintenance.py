#!/usr/bin/env python3
"""
DB 维护脚本 — 数据过期清理 + VACUUM

TTL 策略:
  - llm_usage:            90天 (成本追踪)
  - news_intel:           30天 (新闻情报滚动)
  - fundamental_snapshot: 90天 (基本面快照)
  - analysis_history:    180天 (分析记录)

每周日凌晨运行 (cron: 0 3 * * 0)
"""
import logging
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_analysis.db")

# 表名 → TTL(天)
TTL_CONFIG = {
    "llm_usage": ("called_at", 90),                    # 成本追踪
    "news_intel": ("fetched_at", 30),                  # 新闻情报滚动
    "fundamental_snapshot": ("created_at", 90),         # 基本面快照
    "analysis_history": ("created_at", 180),            # 分析记录
    "stock_daily": ("date", 365),                       # 日线数据 (1年)
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("db_maintenance")


def main():
    if not os.path.exists(DB_PATH):
        logger.warning("DB not found: %s", DB_PATH)
        return

    db_size_before = os.path.getsize(DB_PATH)
    logger.info("DB size before: %s KB", db_size_before // 1024)

    conn = sqlite3.connect(DB_PATH)

    total_deleted = 0

    for table, (time_col, ttl_days) in TTL_CONFIG.items():
        # 检查表是否存在
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            logger.info("Table '%s' not found, skip", table)
            continue

        # 检查 time_col 列是否存在
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        if time_col not in cols:
            logger.info("Table '%s' has no '%s' column, skip", table, time_col)
            continue

        result = conn.execute(
            f"DELETE FROM {table} WHERE {time_col} < datetime('now', '-{ttl_days} days')"
        )

        deleted = result.rowcount
        total_deleted += deleted
        if deleted > 0:
            logger.info("Cleaned %s: %d rows deleted (TTL=%dd)", table, deleted, ttl_days)
        else:
            logger.debug("Cleaned %s: 0 rows to delete", table)

    conn.commit()

    # Close before VACUUM (VACUUM can't run from within a transaction)
    conn.close()

    # VACUUM in a separate connection
    try:
        vac_conn = sqlite3.connect(DB_PATH)
        vac_conn.execute("VACUUM")
        vac_conn.close()
        db_size_after = os.path.getsize(DB_PATH)
        saved = (db_size_before - db_size_after) // 1024
        logger.info(
            "VACUUM complete: %s KB → %s KB (freed %s KB)",
            db_size_before // 1024,
            db_size_after // 1024,
            saved if saved > 0 else 0,
        )
        # 数据增长告警：DB > 200MB 时通知
        if db_size_after > 200 * 1024 * 1024:
            logger.warning(
                "⚠️ DB 超过 200MB (当前: %s MB)，建议检查数据增长趋势并考虑调整 TTL 策略",
                round(db_size_after / (1024 * 1024), 1),
            )
    except Exception as e:
        logger.warning("VACUUM failed: %s", e)

    logger.info("Maintenance done. Total deleted: %d rows", total_deleted)


if __name__ == "__main__":
    main()
