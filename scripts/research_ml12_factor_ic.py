#!/usr/bin/env python3
"""
ML 12因子 IC 分析 — 验证每个因子在实战中的真实预测力

用法:
    .venv/bin/python scripts/research_ml12_factor_ic.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from systems.lynx_vnpy.lynx_signal import fetch_daily_bars, STOCK_CODES, STOCK_NAMES


# ── Factor definitions (复制自 factor_engine.py, 避免复杂导入) ──

@dataclass
class FactorDefinition:
    name: str
    category: str
    display_name: str
    higher_better: bool
    ic: float = 0.0
    ir: float = 0.0
    weight: float = 0.0

CORE_FACTORS = [
    FactorDefinition(name="momentum_reversal",    category="momentum",  display_name="1月反转",         higher_better=False, ic=0.045, ir=0.88, weight=0.35),
    FactorDefinition(name="momentum_spread",      category="momentum",  display_name="动量价差短中",     higher_better=True,  ic=0.038, ir=0.72, weight=0.35),
    FactorDefinition(name="low_volatility",       category="volatility",display_name="低波动",           higher_better=True,  ic=0.042, ir=0.80, weight=0.12),
    FactorDefinition(name="volume_trend",         category="sentiment", display_name="量价配合度",       higher_better=True,  ic=0.035, ir=0.65, weight=0.10),
    FactorDefinition(name="turnover_sentiment",   category="sentiment", display_name="换手率情绪",       higher_better=False, ic=0.050, ir=1.05, weight=0.08),
    FactorDefinition(name="price_position",       category="momentum",  display_name="价格位置60d",      higher_better=False, ic=0.032, weight=0.03),
    FactorDefinition(name="volume_acceleration",  category="sentiment", display_name="量能加速度",       higher_better=True,  ic=0.028, weight=0.03),
    FactorDefinition(name="consecutive_direction",category="momentum",  display_name="连涨连跌偏向",     higher_better=True,  ic=0.025, weight=0.02),
    FactorDefinition(name="volatility_ratio",     category="volatility",display_name="波动率比率短长",   higher_better=False, ic=0.022, weight=0.02),
    FactorDefinition(name="size_factor",          category="quality",   display_name="规模因子小盘溢价", higher_better=True,  ic=0.038, weight=0.04),
    FactorDefinition(name="illiquidity",          category="quality",   display_name="非流动性Amihud",   higher_better=True,  ic=0.041, weight=0.04),
    FactorDefinition(name="max_effect",           category="sentiment", display_name="极端收益MAX",      higher_better=True,  ic=0.035, weight=0.02),
]

FACTOR_NAMES = [f.name for f in CORE_FACTORS]
FACTOR_DISPLAY = {f.name: f.display_name for f in CORE_FACTORS}
FACTOR_DECLARED_IC = {f.name: f.ic for f in CORE_FACTORS}


# ── 因子计算函数 (复制自 factor_engine.py) ──

def _compute_volume_trend(close: np.ndarray, volume: np.ndarray, window: int = 10) -> float:
    n = len(close)
    if n < 2:
        return 0.0
    close_ret = np.diff(close[-window:]) / close[-window:-1]
    vol_ratio = volume[-window + 1:] / np.mean(volume[-window:]) if np.mean(volume[-window:]) > 0 else 0
    valid = min(len(close_ret), len(vol_ratio))
    if valid < 3:
        return 0.0
    corr = np.corrcoef(close_ret[-valid:], vol_ratio[-valid:])[0, 1]
    return corr if not np.isnan(corr) else 0.0

def _compute_volatility_ratio(close: np.ndarray, short: int = 5, long: int = 20) -> float:
    n = len(close)
    if n < long + 1:
        return 0.0
    short_std = np.std(close[-short:])
    long_std = np.std(close[-long:])
    return short_std / long_std if long_std > 0 else 0.0

def _compute_illiquidity(close: np.ndarray, volume: np.ndarray, window: int = 20) -> float:
    n = len(close)
    if n < window or len(volume) < window:
        return 0.0
    returns = np.abs(np.diff(close[-window:]) / close[-window:-1])
    dollar_vol = volume[-window + 1:] * close[-window + 1:]
    ratios = returns / dollar_vol
    return float(np.mean(ratios[~np.isnan(ratios) & ~np.isinf(ratios)])) if any(~np.isnan(ratios)) else 0.0

def _compute_max_effect(close: np.ndarray, window: int = 20) -> float:
    n = len(close)
    if n < window:
        return 0.0
    returns = np.diff(close[-(window + 1):]) / close[-(window + 1):-1]
    return float(np.max(np.abs(returns))) if len(returns) > 0 else 0.0

def _compute_momentum_reversal(close: np.ndarray, window: int = 21) -> float:
    n = len(close)
    if n < window + 1:
        return 0.0
    return (close[-1] / close[-(window + 1)] - 1) * 100

def _compute_momentum_spread(close: np.ndarray, short: int = 5, long: int = 21) -> float:
    n = len(close)
    if n < long + 1:
        return 0.0
    short_ret = (close[-1] / close[-(short + 1)] - 1) * 100
    long_ret  = (close[-1] / close[-(long + 1)] - 1) * 100
    return short_ret - long_ret

def _compute_low_volatility(close: np.ndarray, window: int = 20) -> float:
    n = len(close)
    if n < window:
        return 0.0
    returns = np.diff(close[-window:]) / close[-window:-1]
    return -np.std(returns)  # 负号: 低波动 = 因子值高

def _compute_turnover_sentiment(close: np.ndarray, volume: np.ndarray, window: int = 5) -> float:
    if len(volume) < window:
        return 0.0
    recent = volume[-window:]
    return np.mean(recent) / np.mean(volume) if np.mean(volume) > 0 else 0

def _compute_price_position(close: np.ndarray, window: int = 60) -> float:
    n = len(close)
    if n < window:
        return 0.0
    hi, lo = np.max(close[-window:]), np.min(close[-window:])
    return (close[-1] - lo) / (hi - lo) if (hi - lo) > 0 else 0.5

def _compute_volume_acceleration(volume: np.ndarray, short: int = 5, long: int = 10) -> float:
    if len(volume) < long:
        return 0.0
    return np.mean(volume[-short:]) / np.mean(volume[-long:]) - 1 if np.mean(volume[-long:]) > 0 else 0

def _compute_consecutive_direction(close: np.ndarray, window: int = 5) -> float:
    n = len(close)
    if n < window + 1:
        return 0.0
    returns = np.sign(np.diff(close[-(window + 1):]))
    return float(np.sum(returns)) / window

def _compute_size_factor(close: np.ndarray, volume: np.ndarray, window: int = 20) -> float:
    if len(close) < window or len(volume) < window:
        return 0.0
    avg_amount = float(np.mean(volume[-window:] * close[-window:]))
    if avg_amount <= 0:
        return 0.0
    return -np.log1p(avg_amount / 1e8)

def _compute_max_effect_value(close: np.ndarray, window: int = 20) -> float:
    return _compute_max_effect(close, window)


_FN_MAP = {
    "momentum_reversal":    lambda c, v: _compute_momentum_reversal(c),
    "momentum_spread":      lambda c, v: _compute_momentum_spread(c),
    "low_volatility":       lambda c, v: _compute_low_volatility(c),
    "volume_trend":         _compute_volume_trend,
    "turnover_sentiment":   lambda c, v: _compute_turnover_sentiment(c, v),
    "price_position":       lambda c, v: _compute_price_position(c),
    "volume_acceleration":  lambda c, v: _compute_volume_acceleration(v),
    "consecutive_direction":lambda c, v: _compute_consecutive_direction(c),
    "volatility_ratio":     lambda c, v: _compute_volatility_ratio(c),
    "size_factor":          lambda c, v: _compute_size_factor(c, v),
    "illiquidity":          _compute_illiquidity,
    "max_effect":           lambda c, v: _compute_max_effect(c),
}


def compute_factors(close: np.ndarray, volume: np.ndarray) -> dict[str, float]:
    return {fn: _FN_MAP[fn](close, volume) for fn in FACTOR_NAMES}


# ── 主流程 ──

def main():
    print("=" * 70)
    print("ML 12因子 IC 分析 — 验证实战预测力")
    print(f"股票池: {len(STOCK_CODES)} 只")
    print("=" * 70)

    all_data = {}
    for code in STOCK_CODES:
        df = fetch_daily_bars(code, days=360)
        if df is not None and len(df) >= 120:
            df = df.rename(columns={
                "开盘": "open", "最高": "high", "最低": "low",
                "收盘": "close", "成交量": "volume", "日期": "date",
            })
            df["date"] = pd.to_datetime(df["date"])
            df["code"] = code
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            all_data[code] = df
            print(f"  {code} {STOCK_NAMES.get(code, '')}: {len(df)} 行")
        else:
            print(f"  {code}: 数据不足 ({len(df) if df is not None else 0})")

    # 对齐日期
    all_dates = None
    for df in all_data.values():
        dates = set(str(d.date()) for d in df["date"])
        all_dates = dates if all_dates is None else all_dates & dates
    all_dates = sorted(all_dates)
    print(f"\n公共交易日: {len(all_dates)} ({all_dates[0]} ~ {all_dates[-1]})")

    if len(all_dates) < 60:
        print("公共交易日不足 60 天")
        return

    # 逐日计算因子
    daily_data = []
    for date_str in all_dates:
        date = pd.Timestamp(date_str)
        entries = []
        for code in STOCK_CODES:
            if code not in all_data:
                continue
            df = all_data[code]
            hist = df[df["date"] <= date]
            if len(hist) < 30:
                continue
            close = hist["close"].values.astype(float)
            volume = hist["volume"].values.astype(float)
            if np.any(np.isnan(close)) or np.any(np.isnan(volume)):
                continue
            factors = compute_factors(close, volume)
            if factors:
                factors["code"] = code
                factors["date"] = date_str
                entries.append(factors)
        if len(entries) >= 6:
            daily_data.append(entries)

    print(f"有效交易日 (\u22656 股票): {len(daily_data)} 天")

    # IC 计算
    ic_vals = {fn: [] for fn in FACTOR_NAMES}
    for entries in daily_data:
        date_str = entries[0]["date"]
        idx = all_dates.index(date_str)
        if idx >= len(all_dates) - 1:
            continue
        next_date_str = all_dates[idx + 1]

        rows = []
        for e in entries:
            code = e["code"]
            df = all_data[code]
            today_row = df[df["date"].dt.date == pd.Timestamp(date_str).date()]
            next_row = df[df["date"].dt.date == pd.Timestamp(next_date_str).date()]
            if today_row.empty or next_row.empty:
                continue
            tc = float(today_row.iloc[-1]["close"])
            nc = float(next_row.iloc[-1]["close"])
            ret = (nc / tc - 1) * 100 if tc > 0 else 0
            r = {"code": code, "ret": ret}
            for fn in FACTOR_NAMES:
                r[fn] = e.get(fn, np.nan)
            rows.append(r)

        if len(rows) < 6:
            continue
        dfd = pd.DataFrame(rows)
        for fn in FACTOR_NAMES:
            vals = pd.to_numeric(dfd[fn], errors="coerce")
            rets = pd.to_numeric(dfd["ret"], errors="coerce")
            valid = ~(vals.isna() | rets.isna())
            if valid.sum() >= 6:
                corr = vals[valid].corr(rets[valid], method="spearman")
                ic_vals[fn].append(corr if not np.isnan(corr) else 0)

    # 结果
    print(f"\n{'='*70}")
    print(f"{'因子名':25s} {'中文名':12s} {'声明IC':>8s} {'实测IC':>9s} {'|IC|':>6s} {'天数':>5s} {'方向':>6s}")
    print("-" * 70)

    result = []
    for fn in FACTOR_NAMES:
        vals = [v for v in ic_vals[fn] if v != 0]
        mean_ic = np.mean(vals) if vals else 0
        abs_ic = np.mean([abs(v) for v in vals]) if vals else 0
        dec = FACTOR_DECLARED_IC.get(fn, 0)
        nd = len(vals)
        arrow = "🟢" if mean_ic > 0.01 else ("🔴" if mean_ic < -0.01 else "➡️")
        result.append((fn, mean_ic, abs_ic, dec, nd, arrow))
        print(f"{fn:25s} {FACTOR_DISPLAY[fn]:12s} {dec:>+7.3f} {mean_ic:>+8.4f} {abs_ic:>5.4f} {nd:4d}  {arrow}")

    print(f"\n{'='*70}")
    keep = [r for r in result if r[2] >= 0.03]
    review = [r for r in result if 0.015 <= r[2] < 0.03]
    remove = [r for r in result if r[2] < 0.015]

    for label, items, icon in [("保留", keep, "✅"), ("待审查", review, "🟡"), ("建议裁剪", remove, "🔴")]:
        if items:
            print(f"\n{icon} {label}:")
            for fn, _, abs_ic, _, _, _ in items:
                print(f"   {fn:25s} |IC|={abs_ic:.4f} {FACTOR_DISPLAY[fn]}")

    print(f"\n整体: 平均 |IC| = {np.mean([r[2] for r in result]):.4f} ({len(keep)}保/{len(review)}审/{len(remove)}裁)")


if __name__ == "__main__":
    main()
