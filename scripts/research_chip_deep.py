#!/usr/bin/env python3
"""
筹码 × 换手率深度挖掘 — 多维切片分析

新增维度:
  1. 市场状态 (trending_up/down/sideways/volatile)
  2. 量能趋势 (expanding/shrinking)
  3. 板块相对强度
  4. MA 排列状态
  5. 资本流向配合

用法:
    python scripts/research_chip_deep.py
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB = str(Path(__file__).resolve().parent.parent / "data" / "data_warehouse.db")


def _get_codes() -> list[str]:
    conn = sqlite3.connect(DB)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_ohlcv WHERE turnover > 0 ORDER BY stock_code"
    ).fetchall()]
    conn.close()
    return codes


def _classify_regime(returns_20d: list[float], vol_20d: float) -> str:
    """简单市场状态分类."""
    avg_ret = float(np.mean(returns_20d))
    if avg_ret > 0.02 and vol_20d < 0.015:
        return "trending_up"
    elif avg_ret < -0.02 and vol_20d < 0.015:
        return "trending_down"
    elif vol_20d > 0.025:
        return "volatile"
    else:
        return "sideways"


def main():
    codes = _get_codes()
    logger.info("股票数: %d", len(codes))

    # 加载行业
    conn = sqlite3.connect(DB)
    ind_map = {}
    for r in conn.execute("SELECT stock_code, industry FROM fundamentals WHERE industry IS NOT NULL AND industry != ''"):
        ind_map[r[0]] = r[1]
    conn.close()

    all_records: list[dict] = []

    for code in codes:
        conn = sqlite3.connect(DB)
        ohlcv = conn.execute(
            "SELECT date, close, volume, high, low, turnover "
            "FROM daily_ohlcv WHERE stock_code=? ORDER BY date", (code,)
        ).fetchall()
        chip = conn.execute(
            "SELECT date, profit_ratio, avg_cost, concentration "
            "FROM chip_distribution WHERE stock_code=? ORDER BY date", (code,)
        ).fetchall()
        cap = conn.execute(
            "SELECT date, main_net_flow FROM capital_flows "
            "WHERE stock_code=? ORDER BY date", (code,)
        ).fetchall()
        conn.close()

        if len(ohlcv) < 100 or len(chip) < 20:
            continue

        chip_by_date = {r[0]: r for r in chip}
        chip_dates = sorted(chip_by_date.keys())
        cap_by_date = {r[0]: float(r[1] or 0) for r in cap}

        for i in range(80, len(ohlcv) - 20, 5):
            eval_date = ohlcv[i][0]

            # 找到芯片
            idx = -1
            for j, cd in enumerate(chip_dates):
                if cd <= eval_date:
                    idx = j
            if idx < 5:
                continue

            cur = chip_by_date[chip_dates[idx]]
            cur_conc = float(cur[3]) if cur[3] else 0
            cur_profit = float(cur[1]) if cur[1] else 0
            cur_cost = float(cur[2]) if cur[2] else 0
            cur_close = float(ohlcv[i][1])

            # 换手率分位
            lookback = [float(r[5] or 0) for r in ohlcv[max(0, i - 60):i + 1]]
            if len(lookback) < 30:
                continue
            turn_pct = sum(1 for v in lookback[:-1] if v < lookback[-1]) / max(len(lookback[:-1]), 1)

            # 价格位置
            prices = np.array([float(r[1]) for r in ohlcv[max(0, i - 60):i + 1]], dtype=float)
            high_60, low_60 = float(np.max(prices)), float(np.min(prices))
            price_pos = (cur_close - low_60) / (high_60 - low_60) if high_60 > low_60 else 0.5

            # 量能趋势
            vols = np.array([float(r[2]) for r in ohlcv[max(0, i - 20):i + 1]], dtype=float)
            vol_trend = float(np.mean(vols[-5:]) / max(np.mean(vols[-20:-5]), 1))

            # MA排列 (5/10/20)
            ma5 = float(np.mean(prices[-5:])) if len(prices) >= 5 else 0
            ma10 = float(np.mean(prices[-10:])) if len(prices) >= 10 else 0
            ma20 = float(np.mean(prices[-20:])) if len(prices) >= 20 else 0
            ma_bullish = ma5 > ma10 > ma20
            ma_bearish = ma5 < ma10 < ma20

            # 市场状态 (用全市场等权平均收益近似)
            rets_20d = []
            for j in range(max(0, i - 20), i):
                rets_20d.append(float(ohlcv[j][4] - ohlcv[j-1][4]) / ohlcv[j-1][4] if j > 0 and ohlcv[j-1][4] > 0 else 0)
            vol_20d = float(np.std(rets_20d)) if rets_20d else 0
            regime = _classify_regime(rets_20d, vol_20d)

            # 板块相对强度: 该股票所在板块其他股票的平均收益
            industry = ind_map.get(code, "其他")
            # 简化: 用个股本身的20日收益

            # 资本流向
            main_net = cap_by_date.get(eval_date, 0)
            has_cap_inflow = main_net > 0

            # Forward return
            fwd = (float(ohlcv[i + 19][1]) - cur_close) / cur_close if len(ohlcv) > i + 19 else 0

            all_records.append({
                "code": code, "industry": industry,
                "regime": regime,
                "price_pos": price_pos,
                "turn_pct": turn_pct,
                "conc": cur_conc,
                "profit": cur_profit,
                "cost_dev": (cur_close - cur_cost) / cur_cost if cur_cost > 0 else 0,
                "vol_trend": vol_trend,
                "ma_bullish": ma_bullish, "ma_bearish": ma_bearish,
                "cap_inflow": has_cap_inflow,
                "fwd_ret_20d": fwd,
            })

    logger.info("总评估点: %d", len(all_records))

    # ── 报表函数 ──
    def print_group(label: str, group: list[dict], sort_key: str = "fwd_ret_20d"):
        if not group:
            print(f"{label:<30} 无数据")
            return
        rets = np.array([r["fwd_ret_20d"] for r in group])
        reg = [r["regime"] for r in group]
        print(f"{label:<30} n={len(group):>4}  avg={float(np.mean(rets)):>+7.2%} "
              f"median={float(np.median(rets)):>+7.2%} "
              f"胜率={float(np.mean(rets > 0)):.1%} "
              f"regime={max(set(reg), key=reg.count) if reg else '?'}")

    # ── 1. 按市场状态分层 ──
    print(f"\n{'=' * 80}")
    print(f"  1. 市场状态 × 交叉信号")
    print(f"{'=' * 80}")
    for regime in ["trending_up", "trending_down", "sideways", "volatile"]:
        sub = [r for r in all_records if r["regime"] == regime]
        if not sub:
            continue
        print(f"\n  [{regime}] n={len(sub)}")
        for name, cond in [
            ("A3/A6 (低位+高换+集中)", lambda r: r["price_pos"] < 0.25 and r["turn_pct"] > 0.70 and r["conc"] < 0.12),
            ("B2/B6 (高位+高换+发散)", lambda r: r["price_pos"] > 0.75 and r["turn_pct"] > 0.70 and r["conc"] > 0.25),
            ("集中<12%", lambda r: r["conc"] < 0.12),
            ("分散>25%", lambda r: r["conc"] > 0.25),
            ("获利>80%", lambda r: r["profit"] > 0.80),
            ("获利<20%", lambda r: r["profit"] < 0.20),
        ]:
            g = [r for r in sub if cond(r)]
            print_group(f"    {name}", g)

    # ── 2. 量能趋势 × 筹码 ──
    print(f"\n{'=' * 80}")
    print(f"  2. 量能趋势 (vol_trend) × 筹码集中度")
    print(f"{'=' * 80}")
    for vt_label, vt_cond in [
        ("放量(vol_trend>1.5)", lambda r: r["vol_trend"] > 1.5),
        ("缩量(vol_trend<0.7)", lambda r: r["vol_trend"] < 0.7),
        ("正常(0.7~1.5)", lambda r: 0.7 <= r["vol_trend"] <= 1.5),
    ]:
        sub = [r for r in all_records if vt_cond(r)]
        print(f"\n  [{vt_label}] n={len(sub)}")
        for name, cond in [
            ("集中<12%+获利>50%", lambda r: r["conc"] < 0.12 and r["profit"] > 0.50),
            ("集中<12%+获利<20%", lambda r: r["conc"] < 0.12 and r["profit"] < 0.20),
            ("分散>25%+获利>50%", lambda r: r["conc"] > 0.25 and r["profit"] > 0.50),
            ("分散>25%+获利<20%", lambda r: r["conc"] > 0.25 and r["profit"] < 0.20),
        ]:
            g = [r for r in sub if cond(r)]
            print_group(f"    {name}", g)

    # ── 3. MA排列 × 芯片 ──
    print(f"\n{'=' * 80}")
    print(f"  3. MA排列 × 筹码集中度")
    print(f"{'=' * 80}")
    for ma_label, ma_cond in [("多头排列 (MA5>MA10>MA20)", lambda r: r["ma_bullish"]),
                               ("空头排列 (MA5<MA10<MA20)", lambda r: r["ma_bearish"]),
                               ("交叉/粘合", lambda r: not r["ma_bullish"] and not r["ma_bearish"])]:
        sub = [r for r in all_records if ma_cond(r)]
        print(f"\n  [{ma_label}] n={len(sub)}")
        for name, cond in [
            ("A3/A6 (低位+高换+集中)", lambda r: r["price_pos"] < 0.25 and r["turn_pct"] > 0.70 and r["conc"] < 0.12),
            ("集中<12%", lambda r: r["conc"] < 0.12),
            ("分散>25%", lambda r: r["conc"] > 0.25),
        ]:
            g = [r for r in sub if cond(r)]
            print_group(f"    {name}", g)

    # ── 4. 行业 × 信号最强组合 ──
    print(f"\n{'=' * 80}")
    print(f"  4. 行业 × A3/A6 信号（仅显示>=3次触发的行业）")
    print(f"{'=' * 80}")
    ind_sigs: dict[str, list[float]] = defaultdict(list)
    for r in all_records:
        if r["price_pos"] < 0.25 and r["turn_pct"] > 0.70 and r["conc"] < 0.12:
            ind_sigs[r["industry"]].append(r["fwd_ret_20d"])
    for ind, rets in sorted(ind_sigs.items(), key=lambda x: -len(x[1])):
        if len(rets) >= 3:
            arr = np.array(rets)
            print(f"  {ind:<16} n={len(rets):>3}  avg={float(np.mean(arr)):>+7.2%} 胜率={float(np.mean(arr>0)):.1%}")

    print()


if __name__ == "__main__":
    main()
