#!/usr/bin/env python3
"""
高中间峰双底因子 vs 现有 12 因子的相关性检验

计算:
1. 高中间峰信号与各因子的 Spearman 相关性
2. 各因子与 forward return 的 Spearman IC
3. 高中间峰的边际信息增量（控制其他因子后的部分相关）

用法:
    python scripts/research_pattern_factor_correlation.py
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from datetime import date, timedelta

import numpy as np
from scipy.stats import spearmanr

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "data_warehouse.db"
FACTOR_ENGINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "systems" / "MindLynx-Aistock" / "src" / "core" / "factor_engine.py"
)


def _get_conn():
    return sqlite3.connect(str(DB_PATH), timeout=10)


# ── 数据加载 ──


def load_ohlcv(code: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT date, open, high, low, close, volume, amount, pct_chg, turnover "
        "FROM daily_ohlcv WHERE stock_code=? ORDER BY date", (code,)
    ).fetchall()
    conn.close()
    return [dict(zip(["date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"], r)) for r in rows]


# ── 双底检测（与 research_double_bottom.py 保持一致）──


def detect_double_bottom(data: list[dict], window: int = 120) -> list[dict]:
    """滑动窗口检测双底形态, 返回带分类和 forward return 的信号列表."""
    n = len(data)
    signals = []
    for start in range(0, n - window, 5):
        chunk = data[start:start + window]
        c = np.array([d["close"] for d in chunk], dtype=float)
        h = np.array([d["high"] for d in chunk], dtype=float)
        l = np.array([d["low"] for d in chunk], dtype=float)

        recent_lows = sorted(range(len(c)), key=lambda i: l[i])[:5]
        if len(recent_lows) < 2:
            continue
        lo1, lo2 = sorted(recent_lows[:2])
        if lo2 - lo1 < 5:
            continue
        if abs(l[lo1] - l[lo2]) / max(l[lo1], l[lo2]) >= 0.03:
            continue
        mid_high = float(np.max(h[lo1:lo2 + 1]))
        if mid_high <= l[lo1] * 1.03:
            continue

        left_shoulder = float(np.max(h[:lo1 + 1])) if lo1 >= 2 else h[0]
        is_elevated = mid_high > left_shoulder * 1.01

        if is_elevated:
            signal_date = data[start + lo2]["date"]
            signals.append({
                "date": signal_date,
                "code": data[0].get("stock_code", ""),
                "type": "elevated",
            })
    return signals


# ── 因子计算（调用 factor_engine）──
# 需要评估时间点的因子值。我们滑动窗口计算。


def compute_factors_for_date(code: str, lookback_data: list[dict], factor_names: list[str]) -> dict[str, float]:
    """在给定时间点计算各因子值（复用现有因子计算逻辑的近似实现）。"""
    close = np.array([d["close"] for d in lookback_data], dtype=float)
    volume = np.array([d["volume"] for d in lookback_data], dtype=float)
    high = np.array([d["high"] for d in lookback_data], dtype=float)
    low = np.array([d["low"] for d in lookback_data], dtype=float)
    amount = np.array([d.get("amount", 0) for d in lookback_data], dtype=float)

    return {
        "momentum_reversal": _compute_momentum_reversal(close, 21),
        "momentum_spread": _compute_momentum_spread(close, 5, 20),
        "low_volatility": _compute_low_volatility(close, 20),
        "volume_trend": _compute_volume_trend(volume, 20),
        "turnover_sentiment": _compute_turnover_sentiment(volume, 20),
        "price_position": _compute_price_position(close, 60),
        "volume_acceleration": _compute_volume_acceleration(volume, 5, 20),
        "consecutive_direction": _compute_consecutive_direction(close, 5),
        "volatility_ratio": _compute_volatility_ratio(close, 5, 20),
        "size_factor": _compute_size_factor(close, volume, amount[-1] if len(amount) > 0 else 0),
        "illiquidity": _compute_illiquidity(close, amount, 20),
        "max_effect": _compute_max_effect(close, 5),
    }


# 需要实现的缺省因子函数


def _compute_momentum_spread(close, short_w, long_w):
    if len(close) < long_w:
        return 0.0
    short_ma = float(np.mean(close[-short_w:]))
    long_ma = float(np.mean(close[-long_w:]))
    return (short_ma - long_ma) / long_ma if long_ma > 0 else 0.0


def _compute_momentum_reversal(close, window):
    if len(close) < window:
        return 0.0
    return (close[0] - close[-1]) / close[0] if close[0] > 0 else 0.0


def _compute_low_volatility(close, window):
    if len(close) < window:
        return 0.0
    rets = np.diff(close[-window:]) / close[-window:-1]
    return float(np.std(rets) * np.sqrt(252)) if len(rets) > 0 else 0.0


def _compute_turnover_sentiment(volume, window):
    if len(volume) < window + 1:
        return 1.0
    recent = volume[-1]
    avg_vol = float(np.mean(volume[-(window + 1):-1]))
    return recent / avg_vol if avg_vol > 0 else 1.0


def _compute_volume_trend(volume, window):
    if len(volume) < window + 1:
        return 0.0
    v = volume[-window:]
    avg = float(np.mean(v))
    return avg


def _compute_price_position(close, window):
    if len(close) < window:
        return 0.5
    recent = close[-window:]
    min_c = float(np.min(recent))
    max_c = float(np.max(recent))
    if max_c - min_c < 1e-8:
        return 0.5
    return (close[-1] - min_c) / (max_c - min_c)


def _compute_volume_acceleration(volume, short_w, long_w):
    if len(volume) < long_w:
        return 0.0
    s = float(np.mean(volume[-short_w:]))
    l = float(np.mean(volume[-long_w:]))
    return (s - l) / l if l > 0 else 0.0


def _compute_consecutive_direction(close, window):
    if len(close) < window:
        return 0.0
    diffs = np.diff(close[-window:])
    up = float(np.sum(diffs > 0))
    down = float(np.sum(diffs < 0))
    total = up + down
    return (up - down) / total if total > 0 else 0.0


def _compute_volatility_ratio(close, short_w, long_w):
    if len(close) < long_w:
        return 0.0
    short_vol = float(np.std(np.diff(close[-short_w:]) / close[-short_w:-1]))
    long_vol = float(np.std(np.diff(close[-long_w:]) / close[-long_w:-1]))
    return short_vol / long_vol if long_vol > 0 else 1.0


def _compute_size_factor(close, volume, amount):
    return -np.log(max(amount, 1))


def _compute_illiquidity(close, amount, window):
    if len(close) < window or len(amount) < window:
        return 0.0
    rets = np.abs(np.diff(close[-window:]) / close[-window:-1])
    vols = amount[-window + 1:]
    min_len = min(len(rets), len(vols))
    if min_len == 0:
        return 0.0
    return float(np.mean(rets[:min_len] / vols[:min_len])) if np.sum(vols[:min_len]) > 0 else 0.0


def _compute_max_effect(close, window):
    if len(close) < window:
        return 0.0
    rets = np.diff(close[-window:]) / close[-window:-1]
    return float(np.max(rets))


# ── 主程序 ──


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    conn = _get_conn()
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_ohlcv ORDER BY stock_code"
    ).fetchall()]
    conn.close()
    logger.info("股票数: %d", len(codes))

    factor_names = [
        "momentum_reversal", "momentum_spread", "low_volatility",
        "volume_trend", "turnover_sentiment", "price_position",
        "volume_acceleration", "consecutive_direction", "volatility_ratio",
        "size_factor", "illiquidity", "max_effect",
    ]

    # 收集: 每只股票每个评估时间点的因子值 + 是否有高中间峰信号 + forward return
    records: list[dict] = []

    for code in codes:
        data = load_ohlcv(code)
        if len(data) < 180:
            continue

        signals = detect_double_bottom(data)
        signal_dates = {s["date"] for s in signals}

        # 对每个滑动窗口位置计算因子
        for i in range(60, len(data) - 20, 5):
            lookback = data[i - 60:i]
            eval_date = data[i]["date"]
            fwd_ret = (data[i + 19]["close"] - data[i]["close"]) / data[i]["close"]

            factors = compute_factors_for_date(code, lookback, factor_names)
            factors["is_elevated"] = 1.0 if eval_date in signal_dates else 0.0
            factors["forward_return_20d"] = fwd_ret
            factors["stock_code"] = code
            factors["date"] = eval_date
            records.append(factors)

        if len(records) % 1000 == 0:
            logger.info("  已收集 %d 条记录...", len(records))

    logger.info("总计 %d 条记录", len(records))

    # ── 相关性矩阵 ──
    print("\n" + "=" * 70)
    print("  高中间峰因子 vs 现有 12 因子 — Spearman 相关性")
    print("=" * 70)

    factor_arrays: dict[str, np.ndarray] = {}
    for name in factor_names + ["is_elevated", "forward_return_20d"]:
        factor_arrays[name] = np.array([r[name] for r in records], dtype=float)

    # 高中间峰与各因子的相关性
    print(f"\n{'因子':<24} {'Spearman ρ':>10} {'p-value':>10} {'显著':>6}")
    print("-" * 52)
    for name in factor_names:
        rho, p = spearmanr(factor_arrays["is_elevated"], factor_arrays[name])
        sig = "✅" if p < 0.05 else "  "
        print(f"{name:<24} {rho:>+10.4f} {p:>10.4f} {sig:>6}")

    # 各因子的 IC (与 forward return 的相关性)
    print(f"\n\n{'=' * 70}")
    print("  各因子的 IC (20日 forward return Spearman)")
    print("=" * 70)
    print(f"\n{'因子':<24} {'IC (ρ)':>10} {'p-value':>10} {'显著':>6}")
    print("-" * 52)
    for name in factor_names + ["is_elevated"]:
        rho, p = spearmanr(factor_arrays[name], factor_arrays["forward_return_20d"])
        sig = "✅" if p < 0.05 else "  "
        print(f"{name:<24} {rho:>+10.4f} {p:>10.4f} {sig:>6}")

    # 高中间峰的边际信息: 控制所有因子后的部分相关
    # 用线性回归: is_elevated 能否被其他因子解释？
    from sklearn.linear_model import LinearRegression

    X = np.column_stack([factor_arrays[n] for n in factor_names])
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    y = np.nan_to_num(factor_arrays["is_elevated"], nan=0.0)
    fwd = np.nan_to_num(factor_arrays["forward_return_20d"], nan=0.0)

    model = LinearRegression()
    model.fit(X, y)
    r2 = model.score(X, y)
    print(f"\n\n{'=' * 70}")
    print("  线性回归: 现有因子解释高中间峰信号的程度")
    print("=" * 70)
    print(f"  R² = {r2:.4f}")
    print(f"  解释: 现有因子 {'可以' if r2 > 0.5 else '无法'}完全解释高中间峰信号")
    print(f"  结论: 高中间峰具有{'少量' if r2 > 0.3 else '显著'}的独立信息增量")

    # 控制因子后的边际 IC
    residuals = y - model.predict(X)
    marginal_rho, marginal_p = spearmanr(residuals, fwd)
    print(f"\n  控制其他因子后, 高中间峰残差的 IC:")
    print(f"  Spearman ρ = {marginal_rho:+.4f},  p = {marginal_p:.4f}")
    print(f"  {'✅ 独立信号, 建议加入因子层' if marginal_p < 0.05 else '⚠️ 无独立信息增量, 不需要加因子'}")

    print()


if __name__ == "__main__":
    main()
