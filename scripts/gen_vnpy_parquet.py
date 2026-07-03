#!/usr/bin/env python3
"""
从 stock_daily DB 生成 vnpy BacktestingEngine 所需的 parquet 数据。

输出: data/vnpy_lab/daily/{code}.parquet (标准 OHLCV 格式)
依赖: sqlite3 + pandas (fusion venv 已有)

用法:
    .venv/bin/python scripts/gen_vnpy_parquet.py                     # 全量
    .venv/bin/python scripts/gen_vnpy_parquet.py --days 60           # 最近 N 天
    .venv/bin/python scripts/gen_vnpy_parquet.py --code 000592       # 单只
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ML_DB = PROJECT_ROOT / "systems/MindLynx-Aistock/data/stock_analysis.db"
OUTPUT_DIR = PROJECT_ROOT / "data/vnpy_lab/daily"


def load_from_db(days: int | None = None, code: str | None = None) -> pd.DataFrame:
    """从 stock_daily 加载数据，返回统一 DataFrame。"""
    if not ML_DB.exists():
        print(f"❌ DB 不存在: {ML_DB}")
        sys.exit(1)

    conn = sqlite3.connect(str(ML_DB))
    where_clauses = []
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where_clauses.append(f"date >= '{cutoff}'")
    if code:
        where_clauses.append(f"code = '{code}'")

    where = " AND ".join(where_clauses) if where_clauses else "1=1"
    query = f"""
        SELECT code, date, open, high, low, close, volume, amount
        FROM stock_daily
        WHERE {where}
        ORDER BY code, date
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    # vnpy 要求的 datetime 列：统一为 09:30 开盘时间
    df["datetime"] = pd.to_datetime(df["date"] + " 09:30:00")
    # 列重命名以匹配 vnpy 命名
    df.rename(columns={"amount": "turnover"}, inplace=True)
    return df


def save_to_parquet(df: pd.DataFrame) -> list[str]:
    """按股票代码分拆为独立的 parquet 文件。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["datetime", "open", "high", "low", "close", "volume", "turnover"]
    saved = []
    for code, grp in df.groupby("code"):
        # vnpy 用交易所后缀: 6/5/9 → SH, 其他 → SZ
        suffix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        vt_symbol = f"{code}.{suffix}"
        out = grp[cols].sort_values("datetime")
        out.to_parquet(OUTPUT_DIR / f"{vt_symbol}.parquet", index=False)
        saved.append(vt_symbol)
    return saved


def main():
    parser = argparse.ArgumentParser(description="生成 vnpy 回测 parquet 数据")
    parser.add_argument("--days", type=int, default=None, help="最近 N 天（默认全量）")
    parser.add_argument("--code", type=str, default=None, help="单只股票代码")
    args = parser.parse_args()

    print(f"📂 数据源: {ML_DB}")
    print(f"📁 输出目录: {OUTPUT_DIR}")
    df = load_from_db(days=args.days, code=args.code)
    print(f"📊 加载 {len(df)} 行, {df['code'].nunique()} 只股票")

    saved = save_to_parquet(df)
    print(f"✅ 生成 {len(saved)} 个 parquet 文件:")
    for s in saved:
        size = (OUTPUT_DIR / f"{s}.parquet").stat().st_size
        print(f"   {s}.parquet ({size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
