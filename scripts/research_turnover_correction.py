#!/usr/bin/env python3
"""
换手率情绪因子修正验证 — 用真实筹码数据对比:
  - 原版 turnover_sentiment: 高换手 → 看空
  - 修正版: 加入筹码条件反转极端场景

对比两种规则的预测准确率.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB = str(Path(__file__).resolve().parent.parent / "data" / "data_warehouse.db")


def main():
    conn = sqlite3.connect(DB)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_ohlcv WHERE turnover > 0 ORDER BY stock_code"
    ).fetchall()]
    conn.close()
    logger.info("股票数: %d", len(codes))

    records: list[dict] = []

    for code in codes:
        conn = sqlite3.connect(DB)
        ohlcv = conn.execute(
            "SELECT date, close, volume, turnover FROM daily_ohlcv "
            "WHERE stock_code=? AND turnover > 0 ORDER BY date", (code,)
        ).fetchall()
        chip = conn.execute(
            "SELECT date, concentration FROM chip_distribution "
            "WHERE stock_code=? ORDER BY date", (code,)
        ).fetchall()
        conn.close()

        if len(ohlcv) < 80 or len(chip) < 10:
            continue

        chip_by_date = {r[0]: float(r[1] or 0) for r in chip}
        chip_dates = sorted(chip_by_date.keys())

        for i in range(60, len(ohlcv) - 20, 5):
            eval_date = ohlcv[i][0]
            cur_close = float(ohlcv[i][1])

            # 换手率比率 (turnover_sentiment 的算法)
            recent_turns = [float(r[3] or 0) for r in ohlcv[max(0, i - 20):i + 1]]
            if len(recent_turns) < 10:
                continue
            avg_turn = float(np.mean(recent_turns[:-1]))
            turn_ratio = recent_turns[-1] / avg_turn if avg_turn > 0 else 1.0

            # 换手率分位
            lookback = [float(r[3] or 0) for r in ohlcv[max(0, i - 60):i + 1]]
            turn_pct = sum(1 for v in lookback[:-1] if v < lookback[-1]) / max(len(lookback[:-1]), 1)

            # 价格位置
            prices = np.array([float(r[1]) for r in ohlcv[max(0, i - 60):i + 1]], dtype=float)
            high_60, low_60 = float(np.max(prices)), float(np.min(prices))
            price_pos = (cur_close - low_60) / (high_60 - low_60) if high_60 > low_60 else 0.5

            # 筹码集中度
            idx = -1
            for j, cd in enumerate(chip_dates):
                if cd <= eval_date:
                    idx = j
            conc = chip_by_date[chip_dates[idx]] if idx >= 0 else 0

            # Forward return (20天)
            fwd = (float(ohlcv[i + 19][1]) - cur_close) / cur_close if len(ohlcv) > i + 19 else 0

            records.append({
                "code": code, "date": eval_date,
                "turn_ratio": turn_ratio,
                "turn_pct": turn_pct,
                "price_pos": price_pos,
                "conc": conc,
                "fwd_20d": fwd,
            })

    logger.info("评估点: %d", len(records))

    # ── 对比分析 ──
    # 原版 turnover_sentiment: turn_ratio > 1.5 = 看空 (higher_better=False)
    # 修正版: 如果满足反转条件则翻转方向

    results = {}
    for label, cond in [
        ("原版:高换手(>1.5)→看空", lambda r: r["turn_ratio"] > 1.5),
        ("原版:低换手(<0.7)→看多", lambda r: r["turn_ratio"] < 0.7),
        ("A3反转:低位+高换+集中→看多", lambda r: r["price_pos"] < 0.25 and r["turn_pct"] > 0.70 and r["conc"] < 0.12),
        ("A6反转:低位+极高换+集中→看多", lambda r: r["price_pos"] < 0.25 and r["turn_pct"] > 0.90 and r["conc"] < 0.12),
        ("B2/B6加强:高位+高换+发散→强看空", lambda r: r["price_pos"] > 0.75 and r["turn_pct"] > 0.70 and r["conc"] > 0.25),
        ("修正综合:原版高换手-排除A3/A6", lambda r: r["turn_ratio"] > 1.5 and not (r["price_pos"] < 0.25 and r["turn_pct"] > 0.70 and r["conc"] < 0.12)),
    ]:
        if "看空" in label or "强看空" in label:
            predicted_bear = True
        elif "看多" in label:
            predicted_bear = False
        else:
            predicted_bear = True  # 原版高换手

        # 评估
        group = [r for r in records if cond(r)]
        if not group:
            results[label] = {"n": 0, "acc": 0, "avg": 0}
            continue

        actual_bear = [r["fwd_20d"] < 0 for r in group]
        correct = sum(1 for i, r in enumerate(group) if actual_bear[i] == predicted_bear)
        avg_ret = float(np.mean([r["fwd_20d"] for r in group]))

        results[label] = {
            "n": len(group),
            "acc": correct / len(group) * 100,
            "avg": avg_ret,
        }

    print(f"\n{'=' * 70}")
    print(f"  turnover_sentiment 修正对比验证")
    print(f"  总评估点: {len(records)}")
    print(f"{'=' * 70}")
    print(f"\n{'规则':<36} {'次数':>6} {'准确率':>8} {'avg_20d':>10}")
    print("-" * 62)

    for label, result in results.items():
        if result["n"] > 0:
            print(f"{label:<36} {result['n']:>6} {result['acc']:>7.1f}% {result['avg']:>+9.2%}")

    # ── 原版 vs 修正版 方向一致性 ──
    print(f"\n{'─' * 62}")
    print("  原版 vs 修正版 方向冲突分析")
    print(f"{'─' * 62}")

    # 找原版看空但修正版看多的case
    a3_cases = [r for r in records if r["turn_ratio"] > 1.5 and r["price_pos"] < 0.25 and r["turn_pct"] > 0.70 and r["conc"] < 0.12]
    if a3_cases:
        correct_original = sum(1 for r in a3_cases if r["fwd_20d"] < 0) / len(a3_cases) * 100
        correct_revised = sum(1 for r in a3_cases if r["fwd_20d"] > 0) / len(a3_cases) * 100
        print(f"\n  原版看空但A3修正为看多的case: {len(a3_cases)} 个")
        print(f"    原版准确率(看空): {correct_original:.1f}%")
        print(f"    修正准确率(看多): {correct_revised:.1f}%")

    b2_cases = [r for r in records if r["turn_ratio"] > 1.5 and r["price_pos"] > 0.75 and r["turn_pct"] > 0.70 and r["conc"] > 0.25]
    if b2_cases:
        correct_original = sum(1 for r in b2_cases if r["fwd_20d"] < 0) / len(b2_cases) * 100
        correct_revised = sum(1 for r in b2_cases if r["fwd_20d"] < 0) / len(b2_cases) * 100
        print(f"\n  原版看空且B2/B6加强看空的case: {len(b2_cases)} 个")
        print(f"    原版准确率: {correct_original:.1f}%")

    print()


if __name__ == "__main__":
    main()
