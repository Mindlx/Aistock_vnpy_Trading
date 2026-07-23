#!/usr/bin/env python3
"""
筹码历史序列 pilot 验证 — 用真实 ΔC90 × 换手率测试交叉信号

数据: data_warehouse.db
  5 只股票 (000007, 000425, 000541, 000565, 000610)
    各 90 行筹码历史 (2026-03~07)
    各 ~240 行 OHLCV+turnover

用法:
    python scripts/research_chip_pilot.py
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB = str(Path(__file__).resolve().parent.parent / "data" / "data_warehouse.db")
def _get_all_codes() -> list[str]:
    conn = sqlite3.connect(DB)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM chip_distribution ORDER BY stock_code"
    ).fetchall()]
    conn.close()
    return codes



PILOT_CODES = _get_all_codes()


def load_data(code: str):
    conn = sqlite3.connect(DB)
    ohlcv = conn.execute(
        "SELECT date, close, volume, high, low, turnover FROM daily_ohlcv "
        "WHERE stock_code=? ORDER BY date", (code,)
    ).fetchall()
    chip = conn.execute(
        "SELECT date, profit_ratio, avg_cost, concentration FROM chip_distribution "
        "WHERE stock_code=? ORDER BY date", (code,)
    ).fetchall()
    conn.close()
    return ohlcv, chip


def main():
    # 加载行业数据
    conn = sqlite3.connect(DB)
    ind_map = {}
    for r in conn.execute("SELECT stock_code, industry FROM fundamentals WHERE industry IS NOT NULL AND industry != ''"):
        ind_map[r[0]] = r[1]
    conn.close()
    logger.info("行业数据: %d 只", len(ind_map))

    all_records: list[dict] = []

    for code in PILOT_CODES:
        ohlcv, chip = load_data(code)
        if len(ohlcv) < 80 or len(chip) < 20:
            logger.info("%s: 数据不足 (%d OHLCV, %d chip)", code, len(ohlcv), len(chip))
            continue

        # Build date→chip lookup
        chip_by_date: dict[str, tuple] = {r[0]: r for r in chip}
        chip_dates = sorted(chip_by_date.keys())

        # 滑动窗口评估 (每5天)
        for i in range(60, len(ohlcv) - 20, 5):
            eval_date = ohlcv[i][0]

            # 找到芯片数据中 <= eval_date 的最新行
            idx = -1
            for j, cd in enumerate(chip_dates):
                if cd <= eval_date:
                    idx = j
            if idx < 5:
                continue

            # 当前和前 N 天的筹码数据
            cur = chip_by_date[chip_dates[idx]]
            def _prev(delta):
                j = idx - delta
                return chip_by_date[chip_dates[j]] if j >= 0 else cur

            prev5 = _prev(5)
            prev10 = _prev(10)
            prev20 = _prev(20)

            cur_conc = float(cur[3]) if cur[3] else 0
            delta_conc_5d = cur_conc - (float(prev5[3]) if prev5[3] else 0)
            delta_conc_10d = cur_conc - (float(prev10[3]) if prev10[3] else 0)
            delta_conc_20d = cur_conc - (float(prev20[3]) if prev20[3] else 0)

            cur_profit = float(cur[1]) if cur[1] else 0
            cur_cost = float(cur[2]) if cur[2] else 0
            cur_close = float(ohlcv[i][1])
            cost_dev = (cur_close - cur_cost) / cur_cost if cur_cost > 0 else 0
            cost_dev_abs = abs(cost_dev)
            above_cost = cur_close > cur_cost

            # 换手率数据
            lookback = [float(r[5] or 0) for r in ohlcv[max(0, i - 60):i + 1]]
            if len(lookback) < 30:
                continue
            turn_pct = sum(1 for v in lookback[:-1] if v < lookback[-1]) / len(lookback[:-1])

            # 价格位置
            prices = np.array([float(r[1]) for r in ohlcv[max(0, i - 60):i + 1]], dtype=float)
            high_60, low_60 = float(np.max(prices)), float(np.min(prices))
            price_pos = (cur_close - low_60) / (high_60 - low_60) if high_60 > low_60 else 0.5

            # Forward return
            fwd = (float(ohlcv[i + 19][1]) - cur_close) / cur_close if len(ohlcv) > i + 19 else 0

            all_records.append({
                "code": code, "date": eval_date,
                "industry": ind_map.get(code, "其他"),
                "price_pos": price_pos, "turn_pct": turn_pct,
                "conc": cur_conc,
                "delta_conc_5d": delta_conc_5d,
                "delta_conc_10d": delta_conc_10d,
                "delta_conc_20d": delta_conc_20d,
                "profit": cur_profit, "cost_dev": cost_dev,
                "cost_dev_abs": cost_dev_abs, "above_cost": above_cost,
                "fwd_ret_20d": fwd,
            })

    logger.info("总评估点: %d", len(all_records))

    # ── 交叉信号场景测试 ──
    scenarios = {
        "A1:低位+低换+集中": lambda r: r["price_pos"] < 0.25 and r["turn_pct"] < 0.30 and r["conc"] < 0.12,
        "A3:低位+高换+集中": lambda r: r["price_pos"] < 0.25 and r["turn_pct"] > 0.70 and r["conc"] < 0.12,
        "A5:低位+温和+集中": lambda r: r["price_pos"] < 0.25 and 0.30 <= r["turn_pct"] <= 0.70 and r["conc"] < 0.15,
        "A6:低位+极高换+集中": lambda r: r["price_pos"] < 0.25 and r["turn_pct"] > 0.90 and r["conc"] < 0.12,
        "B1/B5:高位+高换+集中": lambda r: r["price_pos"] > 0.75 and r["turn_pct"] > 0.70 and r["conc"] < 0.15,
        "B3:高位+低换+集中": lambda r: r["price_pos"] > 0.75 and r["turn_pct"] < 0.30 and r["conc"] < 0.12,
        "B2/B6:高位+高换+发散": lambda r: r["price_pos"] > 0.75 and r["turn_pct"] > 0.70 and r["conc"] > 0.25,
        "B7:高位+正常+发散": lambda r: r["price_pos"] > 0.75 and 0.30 <= r["turn_pct"] <= 0.70 and r["conc"] > 0.25,
        "ΔC90集中(-5d):筹码快速集中": lambda r: r["delta_conc_5d"] < -0.02,
        "ΔC90发散(+5d):筹码快速发散": lambda r: r["delta_conc_5d"] > 0.02,
        # ── 新维度: ΔC90 长窗口 ──
        "ΔC90集中(-10d/<-3%)": lambda r: r["delta_conc_10d"] < -0.03,
        "ΔC90发散(+10d/>+3%)": lambda r: r["delta_conc_10d"] > 0.03,
        "ΔC90集中(-20d/<-5%)": lambda r: r["delta_conc_20d"] < -0.05,
        "ΔC90发散(+20d/>+5%)": lambda r: r["delta_conc_20d"] > 0.05,
        # ── 新维度: 成本偏离度 ──
        "成本大幅偏离>15%": lambda r: r["cost_dev_abs"] > 0.15,
        "成本偏离5-15%": lambda r: 0.05 <= r["cost_dev_abs"] <= 0.15,
        "成本接近(<5%)": lambda r: r["cost_dev_abs"] < 0.05,
        # ── 新维度: 价格穿越成本 ──
        "价格在成本上方(获利)": lambda r: r["above_cost"],
        "价格在成本下方(套牢)": lambda r: not r["above_cost"],
        "低位+价格刚上穿成本": lambda r: r["price_pos"] < 0.35 and r["above_cost"] and r["cost_dev_abs"] < 0.05,
        "高位+价格刚下穿成本": lambda r: r["price_pos"] > 0.65 and not r["above_cost"] and r["cost_dev_abs"] < 0.05,
        # ── 新维度: 获利比例阈值 ──
        "获利>80%(极度过热)": lambda r: r["profit"] > 0.80,
        "获利<20%(极度冰点)": lambda r: r["profit"] < 0.20,
        "获利20-50%(健康区间)": lambda r: 0.20 <= r["profit"] <= 0.50,
    }

    print(f"\n{'=' * 70}")
    print(f"  真实筹码历史 Pilot 验证 — 交叉信号场景测试")
    print(f"  {len(PILOT_CODES)} 只股票, {len(all_records)} 个评估点")
    print(f"{'=' * 70}")

    print(f"\n{'场景':<26} {'次数':>6} {'胜率':>8} {'avg_20d':>10} {'中位数':>10} {'std':>10}")
    print("-" * 72)

    results = []
    for name, fn in scenarios.items():
        group = [r for r in all_records if fn(r)]
        if not group:
            print(f"{name:<26} {0:>6} {'N/A':>8} {'N/A':>10} {'N/A':>10} {'N/A':>10}")
            continue
        rets = np.array([r["fwd_ret_20d"] for r in group])
        win_rate = float(np.mean(rets > 0))
        avg = float(np.mean(rets))
        med = float(np.median(rets))
        std = float(np.std(rets))
        results.append((name, len(group), win_rate, avg, med, std))
        print(f"{name:<26} {len(group):>6} {win_rate:>7.1%} {avg:>+9.2%} {med:>+9.2%} {std:>9.2%}")

    print(f"\n{'=' * 70}")

    # ── 对比: 横截面的集中 vs 分散 ──
    conc_high = [r for r in all_records if r["conc"] < 0.12]
    conc_low = [r for r in all_records if r["conc"] > 0.25]
    if conc_high and conc_low:
        ch_ret = np.array([r["fwd_ret_20d"] for r in conc_high])
        cl_ret = np.array([r["fwd_ret_20d"] for r in conc_low])
        print(f"\n高集中(<12%) vs 低集中(>25%) 横截面对比:")
        print(f"  高集中: n={len(conc_high)}  avg={float(np.mean(ch_ret)):+.2%} 胜率={float(np.mean(ch_ret>0)):.1%}")
        print(f"  低集中: n={len(conc_low)}  avg={float(np.mean(cl_ret)):+.2%} 胜率={float(np.mean(cl_ret>0)):.1%}")
        print(f"  差: {float(np.mean(ch_ret) - np.mean(cl_ret)):+.2%}")

    # ── ΔC90 效应 ──
    conc_ing = [r for r in all_records if r["delta_conc_5d"] < -0.02]
    conc_dsg = [r for r in all_records if r["delta_conc_5d"] > 0.02]
    if conc_ing and conc_dsg:
        ci_ret = np.array([r["fwd_ret_20d"] for r in conc_ing])
        cd_ret = np.array([r["fwd_ret_20d"] for r in conc_dsg])
        print(f"\nΔC90 集中(<-2%) vs 发散(>+2%) 对比:")
        print(f"  集中趋势: n={len(conc_ing)}  avg={float(np.mean(ci_ret)):+.2%} 胜率={float(np.mean(ci_ret>0)):.1%}")
        print(f"  发散趋势: n={len(conc_dsg)}  avg={float(np.mean(cd_ret)):+.2%} 胜率={float(np.mean(cd_ret>0)):.1%}")
        print(f"  差: {float(np.mean(ci_ret) - np.mean(cd_ret)):+.2%}")

    # ── 行业维度分析 ──
    from collections import defaultdict
    ind_groups: dict[str, list[float]] = defaultdict(list)
    ind_concs: dict[str, list[float]] = defaultdict(list)
    ind_signals: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for r in all_records:
        ind = r["industry"]
        ind_groups[ind].append(r["fwd_ret_20d"])
        ind_concs[ind].append(r["conc"])
        # A3/A6 信号
        if r["price_pos"] < 0.25 and r["turn_pct"] > 0.70 and r["conc"] < 0.12:
            ind_signals[ind]["A3/A6"].append(r["fwd_ret_20d"])
        # B2/B6 信号
        if r["price_pos"] > 0.75 and r["turn_pct"] > 0.70 and r["conc"] > 0.25:
            ind_signals[ind]["B2/B6"].append(r["fwd_ret_20d"])

    print(f"\n{'=' * 70}")
    print(f"  行业维度分析 — 仅显示 >=30 评估点的行业")
    print(f"{'=' * 70}")
    print(f"\n{'行业':<16} {'评估点':>6} {'avg_20d':>10} {'avg_conc':>10} {'A3/A6_n':>8} {'A3/A6_avg':>10} {'B2/B6_n':>8} {'B2/B6_avg':>10}")
    print("-" * 80)

    ind_results = []
    for ind, rets in ind_groups.items():
        if len(rets) < 30:
            continue
        ret_arr = np.array(rets)
        avg_conc = float(np.mean(ind_concs[ind])) * 100
        a3 = ind_signals[ind].get("A3/A6", [])
        b2 = ind_signals[ind].get("B2/B6", [])
        ind_results.append((
            ind, len(rets), float(np.mean(ret_arr)), avg_conc,
            len(a3), float(np.mean(a3)) if a3 else 0,
            len(b2), float(np.mean(b2)) if b2 else 0,
        ))

    ind_results.sort(key=lambda x: -x[2])  # sort by avg_20d desc
    for r in ind_results:
        a3_str = f"{r[5]:+.1%}" if r[4] > 0 else "N/A"
        b2_str = f"{r[7]:+.1%}" if r[6] > 0 else "N/A"
        print(f"{r[0]:<16} {r[1]:>6} {r[2]:>+9.2%} {r[3]:>9.1f}% {r[4]:>8} {a3_str:>10} {r[6]:>8} {b2_str:>10}")

    print()


if __name__ == "__main__":
    main()
