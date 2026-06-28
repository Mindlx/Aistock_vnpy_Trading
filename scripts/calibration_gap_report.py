#!/usr/bin/env python3
"""
评分-文本校准差距报告 (Calibration Gap Report)

对比 LLM 数值评分 (sentiment_score) 与文本建议 (operation_advice) 的方向一致性，
识别 LLM 的"数值-文本校准差距"。

用法:
    python scripts/calibration_gap_report.py           # 全量报告
    python scripts/calibration_gap_report.py --code 601801  # 单只股票
"""

import argparse
import sqlite3
import json
from pathlib import Path
from collections import Counter

DB = Path("systems/MindLynx-Aistock/data/stock_analysis.db")

SCORE_MAP = {"买入": (">", 0), "加仓": (">", 0), "持有": ("=", None),
             "观望": ("<", 0), "减仓": ("<", 0), "卖出": ("<", 0)}


def infer_direction_from_score(score, threshold_bull=52, threshold_bear=49):
    if score is None:
        return None
    if score >= threshold_bull:
        return "up"
    if score <= threshold_bear:
        return "down"
    return "flat"


def infer_direction_from_advice(advice):
    if not advice:
        return None
    advice = advice.strip()
    if any(kw in advice for kw in ["买入", "加仓", "买", "加"]):
        return "up"
    if any(kw in advice for kw in ["卖出", "减仓", "卖", "减"]):
        return "down"
    return "flat"


def main():
    parser = argparse.ArgumentParser(description="Calibration Gap Report")
    parser.add_argument("--code", type=str, default=None, help="Stock code filter")
    parser.add_argument("--limit", type=int, default=500, help="Max records")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB))
    query = """SELECT code, sentiment_score, operation_advice, raw_result, created_at
               FROM analysis_history
               WHERE sentiment_score IS NOT NULL AND operation_advice IS NOT NULL
               AND code NOT IN ('MARKET', '') AND code IS NOT NULL"""
    params = []
    if args.code:
        query += " AND code = ?"
        params.append(args.code)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(args.limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print("无数据")
        return

    total = len(rows)
    same = 0
    diff = 0
    score_up_advice_down = 0
    score_down_advice_up = 0
    score_flat_advice_extreme = 0

    for code, score, advice, raw, created_at in rows:
        sd = infer_direction_from_score(score)
        ad = infer_direction_from_advice(advice)
        if sd == ad:
            same += 1
        else:
            diff += 1
            if sd == "up" and ad == "down":
                score_up_advice_down += 1
            elif sd == "down" and ad == "up":
                score_down_advice_up += 1
            if ad in ("up", "down") and sd == "flat":
                score_flat_advice_extreme += 1

    # Per-stock breakdown
    per_stock = Counter()
    for code, score, advice, raw, created_at in rows:
        sd = infer_direction_from_score(score)
        ad = infer_direction_from_advice(advice)
        key = "一致" if sd == ad else "分歧"
        per_stock[f"{code}:{key}"] += 1

    print("=" * 55)
    print("  LLM 评分-文本校准差距报告")
    print("=" * 55)
    print(f"\n总样本: {total}")
    print(f"方向一致: {same} ({same/total*100:.1f}%)")
    print(f"方向分歧: {diff} ({diff/total*100:.1f}%)")
    print(f"  其中 score↑ advice↓: {score_up_advice_down}")
    print(f"  其中 score↓ advice↑: {score_down_advice_up}")
    print(f"  其中 score= advice极端: {score_flat_advice_extreme}")

    print(f"\n校准吻合率: {same/total*100:.1f}%")

    per_stock_list = [k for k in per_stock if "分歧" in k]
    if per_stock_list:
        print(f"\n分歧最多的股票:")
        for key in sorted(per_stock_list, key=lambda k: per_stock[k], reverse=True)[:5]:
            print(f"  {key}: {per_stock[key]}次")


if __name__ == "__main__":
    main()
