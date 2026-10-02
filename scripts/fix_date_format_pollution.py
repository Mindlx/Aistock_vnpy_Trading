#!/usr/bin/env python3
"""修复数据层日期格式污染 (2026-10-02)

背景
----
不同数据源写入时日期格式不统一:
  - sina/pytdx/tushare 写 plain  'YYYYMMDD'   (纯数字)
  - akshare/efinance/yfinance 写 hyphen 'YYYY-MM-DD'
SQLite 以字符串比较/排序, 混存导致:
  '20260930' > '2026-09-30' (位置4 '0'(0x30) > '-'(0x2D))
  → `ORDER BY date DESC LIMIT n` 返回乱序/过时行, 日期范围查询失真。

受污染表 (实测)
  - data/data_warehouse.db :: capital_flows        (874 hyphen akshare + 195 compact tushare)
  - data/unified_cache/ohlcv_cache.db :: daily_ohlcv (13681 hyphen + 7672 compact)

规范格式 (按各表消费者约定)
  - capital_flows  → hyphen  (akshare 为唯一有效源; 消费者按原样展示)
  - ohlcv_cache    → hyphen  (yfinance 多数 + context_preparer 用 d[-5:] 取 MM-DD)

处置
  - capital_flows: 删除全部 compact(tushare) 行 — 与 akshare 同日值差异达千倍(单位不一致)
  - ohlcv_cache:   删除有 hyphen 孪生的 compact 行; 转换无孪生的 compact 行 → hyphen

用法
  python scripts/fix_date_format_pollution.py            # dry-run (仅报告)
  python scripts/fix_date_format_pollution.py --apply    # 实际执行
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DW = ROOT / "data/data_warehouse.db"
OC = ROOT / "data/unified_cache/ohlcv_cache.db"


def _conn(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(path), timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    return c


def _fmt_stats(c: sqlite3.Connection, table: str) -> tuple[int, int]:
    r = c.execute(
        f"SELECT SUM(CASE WHEN date LIKE '%-%' THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN date LIKE '%-%' THEN 0 ELSE 1 END) FROM {table}"
    ).fetchone()
    return (r[0] or 0, r[1] or 0)


def fix_capital_flows(apply: bool) -> dict:
    """capital_flows: 删除 compact(tushare) 行, 保留 hyphen(akshare)。"""
    c = _conn(DW)
    hy, cp = _fmt_stats(c, "capital_flows")
    by_src = c.execute(
        "SELECT source, CASE WHEN date LIKE '%-%' THEN 'hyphen' ELSE 'compact' END, COUNT(*) "
        "FROM capital_flows GROUP BY 1,2"
    ).fetchall()
    result = {"before_hyphen": hy, "before_compact": cp, "sources": by_src}
    if apply and cp:
        cur = c.execute("DELETE FROM capital_flows WHERE date NOT LIKE '%-%'")
        result["deleted_compact"] = cur.rowcount
        c.commit()
    c.close()
    return result


def fix_ohlcv_cache(apply: bool) -> dict:
    """ohlcv_cache: 删除有 hyphen 孪生的 compact 行; 转换独有 compact → hyphen。"""
    c = _conn(OC)
    hy, cp = _fmt_stats(c, "daily_ohlcv")
    twin = c.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE date NOT LIKE '%-%' AND EXISTS ("
        " SELECT 1 FROM daily_ohlcv d2 WHERE d2.stock_code=daily_ohlcv.stock_code"
        " AND d2.date = printf('%s-%s-%s', substr(daily_ohlcv.date,1,4),"
        " substr(daily_ohlcv.date,5,2), substr(daily_ohlcv.date,7,2)))"
    ).fetchone()[0]
    unique_compact = c.execute(
        "SELECT COUNT(*) FROM daily_ohlcv WHERE date NOT LIKE '%-%' AND NOT EXISTS ("
        " SELECT 1 FROM daily_ohlcv d2 WHERE d2.stock_code=daily_ohlcv.stock_code"
        " AND d2.date = printf('%s-%s-%s', substr(daily_ohlcv.date,1,4),"
        " substr(daily_ohlcv.date,5,2), substr(daily_ohlcv.date,7,2)))"
    ).fetchone()[0]
    result = {"before_hyphen": hy, "before_compact": cp,
              "compact_with_twin": twin, "compact_unique": unique_compact}
    if apply and cp:
        d = c.execute(
            "DELETE FROM daily_ohlcv WHERE date NOT LIKE '%-%' AND EXISTS ("
            " SELECT 1 FROM daily_ohlcv d2 WHERE d2.stock_code=daily_ohlcv.stock_code"
            " AND d2.date = printf('%s-%s-%s', substr(daily_ohlcv.date,1,4),"
            " substr(daily_ohlcv.date,5,2), substr(daily_ohlcv.date,7,2)))"
        )
        u = c.execute(
            "UPDATE daily_ohlcv SET date = printf('%s-%s-%s', substr(date,1,4),"
            " substr(date,5,2), substr(date,7,2)) WHERE date NOT LIKE '%-%'"
        )
        result["deleted_twin"] = d.rowcount
        result["converted_unique"] = u.rowcount
        c.commit()
    c.close()
    return result


def verify() -> dict:
    out = {}
    for name, db, table in [("capital_flows", DW, "capital_flows"),
                            ("ohlcv_cache", OC, "daily_ohlcv")]:
        c = _conn(db)
        hy, cp = _fmt_stats(c, table)
        dup = c.execute(
            f"SELECT COUNT(*) FROM (SELECT stock_code, replace(date,'-','') d, COUNT(*) n "
            f"FROM {table} GROUP BY stock_code, replace(date,'-','') HAVING n>1)"
        ).fetchone()[0]
        out[name] = {"hyphen": hy, "compact": cp, "dup_calendar_days": dup}
        c.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行 (默认 dry-run)")
    args = ap.parse_args()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== 日期格式污染修复 [{mode}] ===")

    print("\n[capital_flows]")
    print(" ", fix_capital_flows(args.apply))
    print("\n[ohlcv_cache]")
    print(" ", fix_ohlcv_cache(args.apply))

    print("\n=== 验证 ===")
    for k, v in verify().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
