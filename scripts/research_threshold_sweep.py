#!/usr/bin/env python3
"""
阈值网格搜索 — 对 B2/B6 和 A3/A6 信号做参数优化

目标: 找到最优的 (price_pos_threshold, turn_pct_threshold, conc_threshold) 组合
方法: 全网格搜索 + 简单多重检验校正

用法:
    python scripts/research_threshold_sweep.py
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB = str(Path(__file__).resolve().parent.parent / "data" / "data_warehouse.db")

# ── 数据加载（复用 research_turnover_correction.py 的加载逻辑）──


def load_records() -> list[dict]:
    conn = sqlite3.connect(DB)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_ohlcv WHERE turnover > 0 ORDER BY stock_code"
    ).fetchall()]
    conn.close()

    records = []
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

            recent_turns = [float(r[3] or 0) for r in ohlcv[max(0, i - 20):i + 1]]
            if len(recent_turns) < 10:
                continue
            avg_turn = float(np.mean(recent_turns[:-1]))
            turn_ratio = recent_turns[-1] / avg_turn if avg_turn > 0 else 1.0

            lookback = [float(r[3] or 0) for r in ohlcv[max(0, i - 60):i + 1]]
            turn_pct = sum(1 for v in lookback[:-1] if v < lookback[-1]) / max(len(lookback[:-1]), 1)

            prices = np.array([float(r[1]) for r in ohlcv[max(0, i - 60):i + 1]], dtype=float)
            high_60, low_60 = float(np.max(prices)), float(np.min(prices))
            price_pos = (cur_close - low_60) / (high_60 - low_60) if high_60 > low_60 else 0.5

            idx = -1
            for j, cd in enumerate(chip_dates):
                if cd <= eval_date:
                    idx = j
            conc = chip_by_date[chip_dates[idx]] if idx >= 0 else 0

            fwd = (float(ohlcv[i + 19][1]) - cur_close) / cur_close if len(ohlcv) > i + 19 else 0

            records.append({
                "price_pos": price_pos, "turn_pct": turn_pct,
                "conc": conc, "fwd_20d": fwd,
            })

    logger.info("总评估点: %d", len(records))
    return records


# ── 网格搜索 ──


def _evaluate(records: list[dict], cond, label: str):
    """评估一组条件的表现."""
    group = [r for r in records if cond(r)]
    if not group:
        return {"n": 0, "acc": 0.0, "avg": 0.0, "win_rate": 0.0}
    rets = np.array([r["fwd_20d"] for r in group])
    return {
        "n": len(group),
        "acc": float(np.mean(rets < 0) * 100),  # bearish accuracy
        "avg": float(np.mean(rets)),
        "win_rate": float(np.mean(rets > 0) * 100),
        "std": float(np.std(rets)),
    }


def main():
    records = load_records()
    baseline = _evaluate(records, lambda r: r["turn_pct"] > 0.50, "所有高换手>50%")

    # ── B2/B6 参数网格: 高位阈值, 换手阈值, 分散阈值 ──
    print(f"\n{'=' * 80}")
    print(f"  B2/B6 阈值网格搜索 — 高位 + 高换手 + 筹码分散")
    print(f"  总评估点: {len(records)}, baseline(>50%分位): "
          f"n={baseline['n']} acc={baseline['acc']:.1f}% avg={baseline['avg']:+.2%}")
    print(f"\n{'price>':>7} {'turn>':>7} {'conc>':>7} {'n':>5} {'acc%':>7} {'avg_20d':>10} {'wins%':>7} {'sharpe':>8}")
    print("-" * 60)

    b2_results = []
    for price_t in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
        for turn_t in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
            for conc_t in [0.15, 0.18, 0.20, 0.22, 0.25, 0.28, 0.30, 0.35, 0.40]:
                r = _evaluate(records, lambda r, pt=price_t, tt=turn_t, ct=conc_t:
                              r["price_pos"] > pt and r["turn_pct"] > tt and r["conc"] > ct, "")
                if r["n"] >= 30:
                    sharpe = r["avg"] / r["std"] * np.sqrt(12) if r["std"] > 0 else 0
                    b2_results.append((price_t, turn_t, conc_t, r))
                    # 只打印前20个最佳(按acc排序)
                    pass

    # 按准确率排序，打印 top 20
    b2_results.sort(key=lambda x: -x[3]["acc"])
    print(f"(Top 20 by accuracy, n>=30)")
    for pt, tt, ct, r in b2_results[:20]:
        sharpe = r["avg"] / r["std"] * np.sqrt(12) if r["std"] > 0 else 0
        print(f"{pt:>5.2f}  {tt:>5.2f}  {ct:>5.2f}  {r['n']:>4}  {r['acc']:>6.1f}% {r['avg']:>+9.2%} {r['win_rate']:>5.1f}% {sharpe:>+7.2f}")

    # 提取原版阈值附近的组合
    print(f"\n{'─' * 60}")
    print(f"  原版阈值附近 (price>0.75, turn>0.70, conc>0.25):")
    orig = [x for x in b2_results if abs(x[0] - 0.75) < 0.01 and abs(x[1] - 0.70) < 0.01 and abs(x[2] - 0.25) < 0.01]
    if orig:
        _, _, _, r = orig[0]
        sharpe = r["avg"] / r["std"] * np.sqrt(12) if r["std"] > 0 else 0
        print(f"  n={r['n']}  acc={r['acc']:.1f}%  avg={r['avg']:+.2%}  sharpe={sharpe:.2f}")
    else:
        print("  (not in top 20)")

    # ── A3/A6 参数网格: 低位阈值, 换手阈值, 集中阈值 ──
    print(f"\n{'=' * 80}")
    print(f"  A3/A6 阈值网格搜索 — 低位 + 高换手 + 筹码集中")
    print(f"\n{'price<':>7} {'turn>':>7} {'conc<':>7} {'n':>5} {'acc%':>7} {'avg_20d':>10} {'wins%':>7} {'sharpe':>8}")
    print("-" * 60)

    a3_results = []
    for price_t in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
        for turn_t in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
            for conc_t in [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25]:
                r = _evaluate(records, lambda r, pt=price_t, tt=turn_t, ct=conc_t:
                              r["price_pos"] < pt and r["turn_pct"] > tt and r["conc"] < ct, "")
                if r["n"] >= 30:
                    sharpe = r["avg"] / r["std"] * np.sqrt(12) if r["std"] > 0 else 0
                    a3_results.append((price_t, turn_t, conc_t, r))

    a3_results.sort(key=lambda x: -x[3]["acc"])
    print(f"(Top 20 by accuracy, n>=30)")
    for pt, tt, ct, r in a3_results[:20]:
        sharpe = r["avg"] / r["std"] * np.sqrt(12) if r["std"] > 0 else 0
        print(f"{pt:>5.2f}  {tt:>5.2f}  {ct:>5.2f}  {r['n']:>4}  {r['acc']:>6.1f}% {r['avg']:>+9.2%} {r['win_rate']:>5.1f}% {sharpe:>+7.2f}")

    # ── 横截面集中度: 最优切分点 ──
    print(f"\n{'=' * 80}")
    print(f"  集中度最优切分点搜索")
    print(f"\n{'conc<':>7} {'n(集中)':>7} {'avg_集中':>10} {'n(分散)>':>7} {'avg_分散':>10} {'差':>10}")
    print("-" * 60)

    for ct in [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25, 0.28, 0.30]:
        conc_high = [r for r in records if r["conc"] < ct]
        conc_low = [r for r in records if r["conc"] > ct + 0.10]
        if len(conc_high) < 30 or len(conc_low) < 30:
            continue
        avg_h = float(np.mean([r["fwd_20d"] for r in conc_high]))
        avg_l = float(np.mean([r["fwd_20d"] for r in conc_low]))
        spread = avg_h - avg_l
        print(f"{ct:>5.2f}  {len(conc_high):>6}  {avg_h:>+9.2%}  {len(conc_low):>6}  {avg_l:>+9.2%}  {spread:>+9.2%}")

    print()


if __name__ == "__main__":
    main()
