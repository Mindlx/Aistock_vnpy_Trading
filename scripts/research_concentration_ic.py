#!/usr/bin/env python3
"""
筹码集中度因子 IC 检验 — 最大化数据验证

计算集中度与未来 20 日收益的 Spearman IC，
测试不同变换方式和窗口。

用法:
    python scripts/research_concentration_ic.py
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB = str(Path(__file__).resolve().parent.parent / "data" / "data_warehouse.db")


def main():
    conn = sqlite3.connect(DB)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM chip_distribution ORDER BY stock_code"
    ).fetchall()]
    conn.close()
    logger.info("股票数: %d", len(codes))

    # 加载全量数据
    all_records: list[dict] = []

    for code in codes:
        conn = sqlite3.connect(DB)
        chip = conn.execute(
            "SELECT date, concentration, profit_ratio, avg_cost "
            "FROM chip_distribution WHERE stock_code=? ORDER BY date", (code,)
        ).fetchall()
        ohlcv = conn.execute(
            "SELECT date, close, turnover FROM daily_ohlcv "
            "WHERE stock_code=? ORDER BY date", (code,)
        ).fetchall()
        conn.close()

        if len(chip) < 10 or len(ohlcv) < 80:
            continue

        ohlcv_map = {r[0]: r for r in ohlcv}
        ohlcv_dates = sorted(ohlcv_map.keys())

        for ci in range(5, len(chip)):
            eval_date = chip[ci][0]
            conc = float(chip[ci][1] or 0)
            profit = float(chip[ci][2] or 0)
            cost = float(chip[ci][3] or 0)

            # 找到对应的 OHLCV 行
            o_idx = -1
            for j, d in enumerate(ohlcv_dates):
                if d >= eval_date:
                    o_idx = j
                    break
            if o_idx < 0 or o_idx + 20 >= len(ohlcv_dates):
                continue

            cur_close = float(ohlcv_map[ohlcv_dates[o_idx]][1])
            # 20 日后的收盘价
            fwd_close = float(ohlcv_map[ohlcv_dates[o_idx + 20]][1])
            fwd_ret = (fwd_close - cur_close) / cur_close if cur_close > 0 else 0

            # 换手率分位
            lookback_turns = [
                float(ohlcv_map[ohlcv_dates[j]][2] or 0)
                for j in range(max(0, o_idx - 60), o_idx + 1)
            ]
            turn_pct = sum(1 for v in lookback_turns[:-1] if v < lookback_turns[-1]) / max(len(lookback_turns[:-1]), 1) if len(lookback_turns) > 1 else 0.5

            all_records.append({
                "code": code, "date": eval_date,
                "conc": conc,
                "conc_inv": -conc,
                "conc_log": np.log(max(conc, 0.001)),
                "conc_bin": 1 if conc < 0.08 else (-1 if conc > 0.18 else 0),
                "profit": profit,
                "cost_dev": abs(cur_close - cost) / cost if cost > 0 else 0,
                "turn_pct": turn_pct,
                "fwd_20d": fwd_ret,
            })

    logger.info("总评估点: %d", len(all_records))

    # ── IC 计算 ──
    def ic(arr_name: str, ret_name: str = "fwd_20d") -> dict:
        x = np.array([r[arr_name] for r in all_records], dtype=float)
        y = np.array([r[ret_name] for r in all_records], dtype=float)
        valid = ~(np.isnan(x) | np.isnan(y))
        if np.sum(valid) < 30:
            return {"rho": 0, "p": 1, "n": 0}
        rho, p = spearmanr(x[valid], y[valid])
        return {"rho": rho, "p": p, "n": int(np.sum(valid))}

    print(f"\n{'=' * 70}")
    print(f"  筹码集中度因子 IC 检验")
    print(f"  {len(all_records)} 评估点, {len(codes)} 只股票")
    print(f"{'=' * 70}")

    print(f"\n{'变换方式':<20} {'IC (ρ)':>10} {'p-value':>10} {'样本':>8} {'显著':>6}")
    print("-" * 56)

    tests = [
        ("原始集中度 (conc)", "conc"),
        ("反转 (1-conc)", "conc_inv"),
        ("对数 (log conc)", "conc_log"),
        ("三分箱 (8%/18%)", "conc_bin"),
        ("获利比例 (profit)", "profit"),
        ("成本偏离度", "cost_dev"),
        ("换手率分位", "turn_pct"),
    ]

    results = []
    for label, field in tests:
        r = ic(field)
        sig = "✅" if r["p"] < 0.05 else "  "
        results.append((label, r))
        print(f"{label:<20} {r['rho']:>+10.4f} {r['p']:>10.4f} {r['n']:>8} {sig:>6}")

    # ── 分位数分层 IC ──
    print(f"\n{'=' * 70}")
    print(f"  集中度分层组合表现 (按 conc 排序后等分 5 组)")
    print(f"{'=' * 70}")

    conc_vals = np.array([r["conc"] for r in all_records])
    rets = np.array([r["fwd_20d"] for r in all_records])
    sorted_idx = np.argsort(conc_vals)
    n = len(all_records)
    group_size = n // 5

    print(f"\n{'组':<6} {'conc范围':>10} {'avg_20d':>10} {'胜率':>8}")
    print("-" * 36)
    for g in range(5):
        start = g * group_size
        end = (g + 1) * group_size if g < 4 else n
        g_rets = rets[sorted_idx[start:end]]
        g_concs = conc_vals[sorted_idx[start:end]]
        avg_r = float(np.mean(g_rets))
        win = float(np.mean(g_rets > 0))
        print(f"Group {g+1:<3} {g_concs[0]:>6.2%}~{g_concs[-1]:>6.2%} {avg_r:>+9.2%} {win:>7.1%}")

    # ── 8% 切分验证 ──
    print(f"\n{'=' * 70}")
    print(f"  8% 切分验证")
    print(f"{'=' * 70}")

    for cut in [0.05, 0.08, 0.10, 0.12, 0.15]:
        high = [r for r in all_records if r["conc"] < cut]
        low = [r for r in all_records if r["conc"] > cut + 0.10]
        if len(high) < 50 or len(low) < 50:
            continue
        h_rets = np.array([r["fwd_20d"] for r in high])
        l_rets = np.array([r["fwd_20d"] for r in low])
        spread = float(np.mean(h_rets)) - float(np.mean(l_rets))
        rho_high, p_high = spearmanr([r["conc"] for r in high], h_rets) if len(high) > 3 else (0, 1)
        rho_low, p_low = spearmanr([r["conc"] for r in low], l_rets) if len(low) > 3 else (0, 1)
        print(f"\n  cut={cut:.0%}: 集中(n={len(high)}) avg={float(np.mean(h_rets)):+.2%} IC={rho_high:.4f}(p={p_high:.4f})")
        print(f"             分散(n={len(low)}) avg={float(np.mean(l_rets)):+.2%} IC={rho_low:.4f}(p={p_low:.4f})")
        print(f"             差={spread:+.2%}")

    print()


if __name__ == "__main__":
    main()
