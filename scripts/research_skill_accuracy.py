#!/usr/bin/env python3
"""
策略级准确率分析 — 从 analysis_history 回算每个策略的独立准确率。

数据流:
  analysis_history.skill_id 以逗号分隔存储激活的策略ID
  按策略聚合, 对比 sentiment_score 方向与 T+1 实际涨跌

用法:
    python scripts/research_skill_accuracy.py
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = str(Path(__file__).resolve().parent.parent / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db")


def main():
    if not Path(DB).exists():
        print(f"数据库不存在: {DB}")
        return

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 读取所有分析记录
    cur.execute("""
        SELECT ah.id, ah.code, ah.sentiment_score, ah.skill_id,
               ah.created_at, sp.pct_chg
        FROM analysis_history ah
        LEFT JOIN stock_daily sp ON sp.code = ah.code
            AND sp.date = date(ah.created_at, '+1 day')
        WHERE ah.sentiment_score IS NOT NULL
          AND sp.pct_chg IS NOT NULL
          AND ah.created_at >= date('now', '-90 days')
        ORDER BY ah.created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("无分析记录")
        return

    # 按策略聚合
    skill_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0, "scores": []})

    for r in rows:
        score = r["sentiment_score"]
        actual = 1 if r["pct_chg"] > 0 else (-1 if r["pct_chg"] < 0 else 0)
        if actual == 0:
            continue

        # 解析 skill_id (逗号分隔)
        raw = r["skill_id"] or "consensus"
        skill_ids = [s.strip() for s in raw.split(",") if s.strip()]

        for sid in skill_ids:
            skill_stats[sid]["total"] += 1
            # 方向判断: >=52 看多, <=48 看空
            if score >= 52 and actual > 0:
                skill_stats[sid]["correct"] += 1
            elif score <= 48 and actual < 0:
                skill_stats[sid]["correct"] += 1
            elif 49 <= score <= 51:
                skill_stats[sid]["correct"] += 1  # 中性视为正确
            skill_stats[sid]["scores"].append(score)

    # 输出结果
    print(f"\n{'=' * 70}")
    print(f"  策略级准确率分析 ({len(rows)} 条分析记录)")
    print(f"{'=' * 70}")
    print(f"\n{'策略ID':<28} {'次数':>6} {'正确':>6} {'准确率':>8} {'avg_score':>10}")
    print("-" * 60)

    results = []
    for sid, stats in skill_stats.items():
        if stats["total"] < 3:
            continue
        acc = stats["correct"] / stats["total"] * 100
        avg_s = sum(stats["scores"]) / len(stats["scores"])
        results.append((sid, stats["total"], stats["correct"], acc, avg_s))

    results.sort(key=lambda x: -x[3])
    for sid, total, correct, acc, avg_s in results:
        print(f"{sid:<28} {total:>6} {correct:>6} {acc:>7.1f}% {avg_s:>9.1f}")

    print()


if __name__ == "__main__":
    main()
