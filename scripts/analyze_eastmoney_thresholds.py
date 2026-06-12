#!/usr/bin/env python3
"""
东方财富评级 w/f 阈值校准分析脚本。

读取 eastmoney_wf_history.csv，关联 stock_daily 次日涨跌幅，
按 w(参与意愿)/f(关注指数) 分桶统计上涨概率，输出最优阈值建议。

用法:
    .venv/bin/python scripts/analyze_eastmoney_thresholds.py

数据要求:
    - data/realtime/eastmoney_wf_history.csv（每日 generate_rating_report.py 自动追加）
    - systems/MindLynx-Aistock/data/stock_analysis.db（stock_daily 表含次日涨跌幅）
"""
from __future__ import annotations

import csv
import sqlite3
import sys

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_wf_history() -> list[dict]:
    """加载 w/f 历史 CSV。"""
    path = PROJECT_ROOT / "data" / "realtime" / "eastmoney_wf_history.csv"
    if not path.exists():
        print(f"❌ 文件不存在: {path}")
        sys.exit(1)
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def get_next_day_returns(code: str, date_str: str, cur: sqlite3.Cursor) -> float | None:
    """获取股票在 date_str 下一交易日的涨跌幅。"""
    cur.execute(
        "SELECT pct_chg FROM stock_daily WHERE code=? AND date > ? ORDER BY date LIMIT 1",
        (code, date_str),
    )
    row = cur.fetchone()
    return row[0] if row else None


def analyze_w_buckets(rows: list[dict]) -> None:
    """按参与意愿(w)分桶统计上涨概率。"""
    db_path = PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 只取 desire 类型行
    desire_rows = [r for r in rows if r["type"] == "desire" and r.get("value")]
    print(f"参与意愿数据: {len(desire_rows)} 条")

    # 统计每个 w 分桶的上涨概率
    # 使用与 _combined_grade 相同的阈值分界点
    buckets = [(0, 20, "<20"), (20, 35, "20-35"), (35, 45, "35-45"),
               (45, 55, "45-55"), (55, 65, "55-65"), (65, 80, "65-80"),
               (80, 101, "≥80")]

    results = []
    for lo, hi, label in buckets:
        bucket = []
        for r in desire_rows:
            try:
                w = float(r["value"])
            except (ValueError, TypeError):
                continue
            if lo <= w < hi:
                ret = get_next_day_returns(r["stock_code"], r["value_date"], cur)
                if ret is not None:
                    bucket.append(ret)

        if not bucket:
            continue
        up = sum(1 for v in bucket if v > 0)
        avg_ret = sum(bucket) / len(bucket)
        results.append((label, len(bucket), up, bucket, avg_ret))

    print(f"\n{'w 分段':>8}  {'样本':>5}  {'上涨':>4}  {'上涨率':>7}  {'次日均涨跌':>10}")
    print("-" * 45)
    for label, n, up, _, avg_ret in results:
        print(f"{label:>8}  {n:>5}  {up:>4}  {up/max(n,1)*100:>6.1f}%  {avg_ret:>+8.2f}%")
    conn.close()


def analyze_f_buckets(rows: list[dict]) -> None:
    """按关注指数(f)分桶统计。"""
    db_path = PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    focus_rows = [r for r in rows if r["type"] == "focus" and r.get("value")]
    print(f"\n关注指数数据: {len(focus_rows)} 条")

    buckets = [(0, 45, "<45"), (45, 55, "45-55"), (55, 65, "55-65"),
               (65, 80, "65-80"), (80, 101, "≥80")]

    results = []
    for lo, hi, label in buckets:
        bucket = []
        for r in focus_rows:
            try:
                f = float(r["value"])
            except (ValueError, TypeError):
                continue
            if lo <= f < hi:
                ret = get_next_day_returns(r["stock_code"], r["value_date"], cur)
                if ret is not None:
                    bucket.append(ret)

        if not bucket:
            continue
        up = sum(1 for v in bucket if v > 0)
        avg_ret = sum(bucket) / len(bucket)
        results.append((label, len(bucket), up, avg_ret))

    print(f"\n{'f 分段':>8}  {'样本':>5}  {'上涨':>4}  {'上涨率':>7}  {'次日均涨跌':>10}")
    print("-" * 45)
    for label, n, up, avg_ret in results:
        print(f"{label:>8}  {n:>5}  {up:>4}  {up/max(n,1)*100:>6.1f}%  {avg_ret:>+8.2f}%")
    conn.close()


def main():
    print("=" * 55)
    print("东方财富评级 w/f 阈值校准分析")
    print("=" * 55)

    rows = load_wf_history()
    print(f"总行数: {len(rows)}")

    analyze_w_buckets(rows)
    analyze_f_buckets(rows)

    print(f"\n{'=' * 55}")
    print("注: 当前为初步分析，每组样本>30 时结果更有参考意义。")
    print("理想样本量: 每组 50+。持续积累到 384+ 总样本后做最终校准。")


if __name__ == "__main__":
    main()
