#!/usr/bin/env python3
"""
W双底高中间峰深度测试 — 行业/市场状态/MA排列/量能等多维切片

复用 research_double_bottom.py 的检测逻辑，叠加维度分析。

用法:
    python scripts/research_double_bottom_deep.py
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


def load_data(code: str):
    conn = sqlite3.connect(DB)
    ohlcv = conn.execute(
        "SELECT date, open, high, low, close, volume, turnover FROM daily_ohlcv "
        "WHERE stock_code=? ORDER BY date", (code,)
    ).fetchall()
    ind = conn.execute(
        "SELECT industry FROM fundamentals WHERE stock_code=?", (code,)
    ).fetchone()
    conn.close()
    return ohlcv, str(ind[0]) if ind and ind[0] else "其他"


def classify_regime(closes_20d: list[float]) -> str:
    if len(closes_20d) < 10:
        return "unknown"
    ret = (closes_20d[-1] - closes_20d[0]) / closes_20d[0]
    vol = float(np.std(np.diff(closes_20d) / np.array(closes_20d[:-1]))) if len(closes_20d) > 1 else 0
    if ret > 0.03 and vol < 0.02:
        return "trending_up"
    elif ret < -0.03 and vol < 0.02:
        return "trending_down"
    elif vol > 0.03:
        return "volatile"
    else:
        return "sideways"


def ma_alignment(closes: list[float]) -> str:
    if len(closes) < 20:
        return "unknown"
    ma5 = float(np.mean(closes[-5:]))
    ma10 = float(np.mean(closes[-10:]))
    ma20 = float(np.mean(closes[-20:]))
    if ma5 > ma10 > ma20:
        return "bullish"
    elif ma5 < ma10 < ma20:
        return "bearish"
    else:
        return "mixed"


def main():
    conn = sqlite3.connect(DB)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_ohlcv ORDER BY stock_code"
    ).fetchall()]
    conn.close()
    logger.info("股票数: %d", len(codes))

    signals: list[dict] = []

    for code in codes:
        ohlcv, industry = load_data(code)
        if len(ohlcv) < 120:
            continue

        c = np.array([float(r[4]) for r in ohlcv], dtype=float)
        h = np.array([float(r[2]) for r in ohlcv], dtype=float)
        l = np.array([float(r[3]) for r in ohlcv], dtype=float)
        v = np.array([float(r[5] or 0) for r in ohlcv], dtype=float)

        for start in range(0, len(ohlcv) - 120, 5):
            window = slice(start, start + 120)
            wc = c[window]
            wh = h[window]
            wl = l[window]
            wv = v[window]

            recent_lows = sorted(range(len(wc)), key=lambda i: wl[i])[:5]
            if len(recent_lows) < 2:
                continue
            lo1, lo2 = sorted(recent_lows[:2])
            if lo2 - lo1 < 5:
                continue
            if abs(wl[lo1] - wl[lo2]) / max(wl[lo1], wl[lo2], 1e-8) >= 0.03:
                continue
            mid_high = float(np.max(wh[lo1:lo2 + 1]))
            if mid_high <= wl[lo1] * 1.03:
                continue

            left_shoulder = float(np.max(wh[:lo1 + 1])) if lo1 >= 2 else wh[0]
            is_elevated = mid_high > left_shoulder * 1.01
            if not is_elevated:
                continue

            # 信号日期 = 第二底
            signal_idx = start + lo2
            if signal_idx + 20 >= len(ohlcv):
                continue

            # 检测时的市场状态
            signal_close = c[signal_idx]
            lookback_closes = c[max(0, signal_idx - 20):signal_idx + 1].tolist()
            regime = classify_regime(lookback_closes)
            ma = ma_alignment(c[max(0, signal_idx - 20):signal_idx + 1].tolist())

            # 量能
            signal_vol = float(wv[lo2]) if lo2 < len(wv) else 0
            avg_vol = float(np.mean(wv[:lo2])) if lo2 > 0 else signal_vol
            vol_ratio = signal_vol / max(avg_vol, 1)

            # Forward return
            fwd = (c[signal_idx + 20] - signal_close) / signal_close

            # 前期涨跌
            prev_ret = (signal_close - c[max(0, signal_idx - 30)]) / max(c[max(0, signal_idx - 30)], 1)

            signals.append({
                "code": code, "industry": industry,
                "regime": regime, "ma": ma,
                "vol_ratio": vol_ratio,
                "prev_ret": prev_ret,
                "fwd_ret_20d": fwd,
            })

    logger.info("高中间峰信号总数: %d", len(signals))

    # ── 透视报表 ──
    def print_group(label: str, items: list[dict]):
        if not items:
            print(f"{label:<30} 无数据")
            return
        rets = np.array([r["fwd_ret_20d"] for r in items])
        print(f"{label:<30} n={len(items):>4}  avg={float(np.mean(rets)):>+9.2%} 胜率={float(np.mean(rets > 0)):.1%}")

    # 1. 行业
    print(f"\n{'=' * 70}")
    print(f"  1. 行业 × 高中间峰 (n>=3)")
    print(f"{'=' * 70}")
    ind_groups = defaultdict(list)
    for s in signals:
        ind_groups[s["industry"]].append(s)
    for ind, items in sorted(ind_groups.items(), key=lambda x: -len(x[1])):
        if len(items) >= 3:
            print_group(f"  {ind}", items)

    # 2. 市场状态
    print(f"\n{'=' * 70}")
    print(f"  2. 市场状态 × 高中间峰")
    print(f"{'=' * 70}")
    for regime in ["trending_up", "trending_down", "sideways", "volatile"]:
        items = [s for s in signals if s["regime"] == regime]
        print_group(f"  {regime}", items)

    # 3. MA排列
    print(f"\n{'=' * 70}")
    print(f"  3. MA排列 × 高中间峰")
    print(f"{'=' * 70}")
    for ma in ["bullish", "bearish", "mixed"]:
        items = [s for s in signals if s["ma"] == ma]
        print_group(f"  {ma}", items)

    # 4. 量能
    print(f"\n{'=' * 70}")
    print(f"  4. 量能 × 高中间峰")
    print(f"{'=' * 70}")
    for label, cond in [
        ("放量突破(vol>2x)", lambda s: s["vol_ratio"] > 2.0),
        ("正常量(0.5~2x)", lambda s: 0.5 <= s["vol_ratio"] <= 2.0),
        ("缩量(<0.5x)", lambda s: s["vol_ratio"] < 0.5),
    ]:
        items = [s for s in signals if cond(s)]
        print_group(f"  {label}", items)

    # 5. 前期涨跌
    print(f"\n{'=' * 70}")
    print(f"  5. 前期30日涨跌 × 高中间峰")
    print(f"{'=' * 70}")
    for label, cond in [
        ("前期大跌(<-15%)", lambda s: s["prev_ret"] < -0.15),
        ("前期小跌(-5~-15%)", lambda s: -0.15 <= s["prev_ret"] < -0.05),
        ("前期盘整(-5~+5%)", lambda s: -0.05 <= s["prev_ret"] <= 0.05),
        ("前期上涨(>+5%)", lambda s: s["prev_ret"] > 0.05),
    ]:
        items = [s for s in signals if cond(s)]
        print_group(f"  {label}", items)

    print()


if __name__ == "__main__":
    main()
