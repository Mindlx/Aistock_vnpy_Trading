#!/usr/bin/env python3
"""数据单位对齐 — 统一为 volume=股 / amount=元 (2026-10-02).

背景: 多源原生单位不一致 (tushare=手/千元, tencent/akshare=手/元, sina=股/元),
      写入端未转换 → 库内混存. 本脚本一次性转换存量数据到规范单位.

幂等: 用 data_migrations 标记表记录已应用, 重复运行自动跳过.

用法:
    python scripts/fix_data_units.py --dry-run   # 预览
    python scripts/fix_data_units.py             # 执行
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_ID = "2026-10-02-align-units-v1"

# (db, table, where, volume_factor, amount_factor)
# 说明: 单位因子经自洽比 amount/(close×volume) 实测确定 (target=1.0)。
#   tushare 日线/指数: ratio≈0.10 (手/千元) → ×100/×1000
#   tencent 实时:      ratio≈100.1 (手/元)   → ×100
#   stock_daily:       中位数 ratio≈1.001, 绝大多数已规范; 仅 605117/605368 为手(ratio 100-212)
CONVERSIONS = [
    # tushare: 手→股 (×100), 千元→元 (×1000)
    ("data/data_warehouse.db", "daily_ohlcv", "source='tushare'", 100, 1000),
    ("data/data_warehouse.db", "index_ohlcv", "source='tushare'", 100, 1000),
    # tencent 实时: 手→股 (×100), amount 已是元
    ("data/data_warehouse.db", "realtime_quotes", "source='tencent'", 100, 1),
    # stock_daily: 仅两只被实测为手的股票 (其余 TushareFetcher 已为股, 不可按源盲转)
    ("data/stock_analysis.db", "stock_daily", "code IN ('605117','605368')", 100, 1),
]


def _ensure_marker(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS data_migrations ("
        "  id TEXT PRIMARY KEY, applied_at TEXT, detail TEXT)"
    )


def _already_applied(conn: sqlite3.Connection) -> bool:
    _ensure_marker(conn)
    row = conn.execute("SELECT 1 FROM data_migrations WHERE id=?", (MIGRATION_ID,)).fetchone()
    return row is not None


def _mark(conn: sqlite3.Connection, detail: str) -> None:
    from datetime import datetime
    conn.execute(
        "INSERT OR REPLACE INTO data_migrations VALUES (?,?,?)",
        (MIGRATION_ID, datetime.now().isoformat(timespec="seconds"), detail),
    )


def _ratio_stats(conn: sqlite3.Connection, table: str, where: str) -> str:
    try:
        r = conn.execute(
            f"SELECT COUNT(*), "
            f"SUM(CASE WHEN amount>0 AND close>0 AND volume>0 "
            f"THEN amount/(close*volume) ELSE NULL END) "
            f"FROM {table} WHERE {where}"
        ).fetchone()
        n = r[0] or 0
        return f"rows={n}"
    except Exception as e:
        return f"n/a ({e})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 按 db 分组
    by_db: dict[str, list] = {}
    for db, table, where, vf, af in CONVERSIONS:
        by_db.setdefault(db, []).append((table, where, vf, af))

    total_changes = 0
    for db_rel, items in by_db.items():
        db_path = ROOT / db_rel
        if not db_path.exists():
            print(f"⚠️  跳过不存在的 {db_rel}")
            continue
        conn = sqlite3.connect(str(db_path))
        try:
            if _already_applied(conn):
                print(f"⏭️  {db_rel}: 迁移 {MIGRATION_ID} 已应用, 跳过")
                continue
            detail_parts = []
            for table, where, vf, af in items:
                before = _ratio_stats(conn, table, where)
                cur = conn.execute(
                    f"UPDATE {table} SET volume = volume * ?, amount = amount * ? WHERE {where}",
                    (vf, af),
                )
                n = cur.rowcount
                total_changes += n
                detail_parts.append(f"{table}[{where[:40]}]: {n} rows (vol×{vf},amt×{af}), {before}")
                print(f"  {db_rel} · {table} · {where}: {n} rows  (vol×{vf}, amount×{af})")
            if args.dry_run:
                conn.rollback()
                print(f"🔍 {db_rel}: dry-run, 已回滚")
            else:
                _mark(conn, "; ".join(detail_parts))
                conn.commit()
                print(f"✅ {db_rel}: 已提交")
        finally:
            conn.close()

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}合计转换 {total_changes} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
