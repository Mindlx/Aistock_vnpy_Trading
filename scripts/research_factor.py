#!/usr/bin/env python3
"""
因子研究脚本：中性化影响分析 + ICIR衰减 vs 静态权重对比

执行:
    python scripts/research_factor.py              # 全量分析
    python scripts/research_factor.py --ic-only     # 仅IC分析
    python scripts/research_factor.py --neutralize  # 仅中性化对比
    python scripts/research_factor.py --icir        # 仅ICIR衰减对比

数据依赖:
    - systems/MindLynx-Aistock/data/stock_analysis.db 的 stock_daily 表
    - 系统无需额外数据源，使用成交额(amount)作为规模代理变量
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ML_DB = PROJECT_ROOT / "systems/MindLynx-Aistock/data/stock_analysis.db"


# ══════════════════════════════════════════════
# 因子计算函数（自包含，不依赖factor_engine）
# ══════════════════════════════════════════════

FACTOR_DEFS: dict[str, dict[str, Any]] = {
    "momentum_reversal":  {"cat": "momentum",  "hb": False, "weight": 0.35},
    "momentum_spread":    {"cat": "momentum",  "hb": True,  "weight": 0.35},
    "low_volatility":     {"cat": "vol",       "hb": True,  "weight": 0.12},
    "volume_trend":       {"cat": "sentiment", "hb": True,  "weight": 0.10},
    "turnover_sentiment": {"cat": "sentiment", "hb": False, "weight": 0.08},
    "price_position":     {"cat": "momentum",  "hb": False, "weight": 0.03},
    "volume_acceleration":{"cat": "sentiment", "hb": True,  "weight": 0.03},
    "consecutive_direction":{"cat": "momentum","hb": True,  "weight": 0.02},
    "volatility_ratio":   {"cat": "vol",       "hb": False, "weight": 0.02},
    "size_factor":        {"cat": "quality",   "hb": True,  "weight": 0.04},
    "illiquidity":        {"cat": "quality",   "hb": True,  "weight": 0.04},
    "max_effect":         {"cat": "sentiment", "hb": True,  "weight": 0.04},
}


def _momentum_reversal(closes: np.ndarray) -> float:
    if len(closes) < 21: return 0.0
    return (closes[0] - closes[-1]) / closes[0]

def _momentum_spread(closes: np.ndarray) -> float:
    if len(closes) < 20: return 0.0
    s = (closes[-1] - closes[-5]) / closes[-5] if len(closes) >=5 else 0
    m = (closes[-1] - closes[-20]) / closes[-20]
    return s - m

def _low_volatility(closes: np.ndarray) -> float:
    if len(closes) < 21: return 0.0
    rets = np.diff(closes[-21:]) / closes[-21:-1]
    return float(np.std(rets)) * np.sqrt(252)

def _volume_trend(closes: np.ndarray, volumes: np.ndarray) -> float:
    if len(closes) < 11 or len(volumes) < 11: return 0.0
    cr = np.diff(closes[-11:]) / closes[-11:-1]
    vr = np.diff(volumes[-11:])
    if np.std(cr) == 0 or np.std(vr) == 0: return 0.0
    return float(np.corrcoef(cr, vr)[0, 1])

def _turnover_sentiment(volumes: np.ndarray) -> float:
    if len(volumes) < 21: return 0.0
    ratio = volumes[-1] / max(np.mean(volumes[-21:-1]), 1e-8)
    return ratio

def _price_position(closes: np.ndarray) -> float:
    if len(closes) < 60: return 0.0
    pos = (closes[-1] - closes.min()) / max(closes.max() - closes.min(), 1e-8)
    return pos

def _volume_acceleration(volumes: np.ndarray) -> float:
    if len(volumes) < 22: return 0.0
    return volumes[-1] / max(np.mean(volumes[-22:-11]), 1e-8) - volumes[-11] / max(np.mean(volumes[-22:-11]), 1e-8)

def _consecutive_direction(closes: np.ndarray) -> float:
    if len(closes) < 11: return 0.0
    rets = np.diff(closes[-11:]) / closes[-11:-1]
    return float(np.sum(rets > 0) - np.sum(rets < 0))

def _volatility_ratio(closes: np.ndarray) -> float:
    if len(closes) < 40: return 0.0
    short = np.std(np.diff(closes[-10:]) / closes[-10:-1])
    long = np.std(np.diff(closes[-40:]) / closes[-40:-1])
    return short / max(long, 1e-8)

def _size_factor(closes: np.ndarray) -> float:
    return -np.log1p(closes[-1]) if len(closes) > 0 else 0.0

def _illiquidity(closes: np.ndarray, volumes: np.ndarray) -> float:
    if len(closes) < 21: return 0.0
    rets = np.abs(np.diff(closes[-21:]) / closes[-21:-1])
    amt = volumes[-20:] * closes[-20:] / 1e8
    valid = amt > 0
    if not valid.any(): return 0.0
    return float(min(np.mean(rets[valid] / amt[valid]), 100.0))

def _max_effect(closes: np.ndarray) -> float:
    if len(closes) < 21: return 0.0
    rets = np.diff(closes[-21:]) / closes[-21:-1]
    return -float(np.max(rets))

FACTOR_FUNCS = {
    "momentum_reversal": lambda c, v: _momentum_reversal(c),
    "momentum_spread": lambda c, v: _momentum_spread(c),
    "low_volatility": lambda c, v: _low_volatility(c),
    "volume_trend": _volume_trend,
    "turnover_sentiment": lambda c, v: _turnover_sentiment(v),
    "price_position": lambda c, v: _price_position(c),
    "volume_acceleration": lambda c, v: _volume_acceleration(v),
    "consecutive_direction": lambda c, v: _consecutive_direction(c),
    "volatility_ratio": lambda c, v: _volatility_ratio(c),
    "size_factor": lambda c, v: _size_factor(c),
    "illiquidity": _illiquidity,
    "max_effect": lambda c, v: _max_effect(c),
}


# ══════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════

def load_stock_dates(min_records: int = 30) -> dict[str, dict[str, Any]]:
    """从stock_daily加载数据，按股票代码分组，返回 {code: {date: {ohlcv}}}."""
    if not ML_DB.exists():
        print(f"❌ DB不存在: {ML_DB}")
        return {}
    conn = sqlite3.connect(str(ML_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT code, date, close, volume, high, low, amount FROM stock_daily "
        "ORDER BY code, date"
    ).fetchall()
    conn.close()

    stocks: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        stocks[r["code"]].append(dict(r))

    # 过滤数据不足的股票
    result: dict[str, dict[str, Any]] = {}
    for code, records in stocks.items():
        if len(records) < min_records:
            continue
        closes = np.array([r["close"] for r in records], dtype=float)
        volumes = np.array([r["volume"] for r in records], dtype=float)
        amounts = np.array([r["amount"] for r in records], dtype=float)
        dates = [r["date"] for r in records]
        result[code] = {
            "closes": closes,
            "volumes": volumes,
            "amounts": amounts,
            "dates": dates,
        }
    print(f"  加载 {len(result)} 只股票, 共 {sum(len(v['dates']) for v in result.values())} 条记录")
    return result


# ══════════════════════════════════════════════
# 因子计算+IC分析
# ══════════════════════════════════════════════

def compute_daily_ic(
    stocks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    逐日计算每个因子的截面Spearman IC。

    每天对全市场股票计算所有因子值→截面标准化→
    与次日收益(pct_chg)计算Spearman秩相关。
    返回 [(date, factor_name, ic, n_stocks), ...]
    """
    min_date_records = max(60, min(len(v["dates"]) for v in stocks.values()) if stocks else 60)
    results: list[dict] = []
    factor_names = list(FACTOR_DEFS.keys())

    # 找到所有股票共同覆盖的日期范围
    all_dates_set: set[str] = set()
    for s in stocks.values():
        all_dates_set.update(s["dates"])
    all_dates = sorted(all_dates_set)

    print(f"  日期范围: {all_dates[0]} ~ {all_dates[-1]} ({len(all_dates)} 天)")

    for di in range(1, len(all_dates)):
        date = all_dates[di]
        prev_date = all_dates[di - 1]

        # 收集该日期所有股票的数据
        raw_factors: dict[str, list[float]] = {f: [] for f in factor_names}
        codes: list[str] = []
        forward_rets: list[float] = []
        amounts: list[float] = []

        for code, sd in stocks.items():
            try:
                idx = sd["dates"].index(date)
            except ValueError:
                continue
            if idx < 60:  # 至少60天历史数据
                continue
            closes = sd["closes"][:idx + 1]
            volumes = sd["volumes"][:idx + 1]

            # 次日收益（需要下一天的数据）
            if idx + 1 >= len(sd["closes"]):
                continue
            fwd_ret = (sd["closes"][idx + 1] - sd["closes"][idx]) / sd["closes"][idx]
            forward_rets.append(float(fwd_ret))

            for fn in factor_names:
                val = FACTOR_FUNCS[fn](closes, volumes)
                raw_factors[fn].append(val)

            codes.append(code)
            amounts.append(float(sd["amounts"][idx]))

        n = len(codes)
        if n < 5:  # 至少5只股票才有统计意义
            continue

        for fn in factor_names:
            vals = np.array(raw_factors[fn])
            valid = ~(np.isnan(vals) | np.isinf(vals))
            if valid.sum() < 5:
                continue

            # 截面标准化
            mean = float(np.mean(vals[valid]))
            std = float(np.std(vals[valid]))
            if std < 1e-8:
                continue
            z = (vals - mean) / std
            z = np.clip(z, -3.0, 3.0)

            # Spearman IC: z_score vs forward_return
            mask = valid & ~np.isnan(forward_rets) & ~np.isinf(forward_rets)
            if mask.sum() < 5:
                continue
            ic = _spearman_r(z[mask], np.array(forward_rets)[mask])
            if not math.isnan(ic) and not math.isinf(ic):
                results.append({
                    "date": date,
                    "factor": fn,
                    "ic": round(ic, 4),
                    "n": int(mask.sum()),
                })

    return results


def _spearman_r(x: np.ndarray, y: np.ndarray) -> float:
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    if np.std(rx) < 1e-10 or np.std(ry) < 1e-10:
        return 0.0
    n = len(rx)
    d = rx - ry
    rho = 1.0 - (6.0 * np.sum(d**2)) / (n * (n**2 - 1.0))
    return float(rho)


# ══════════════════════════════════════════════
# ICIR衰减权重计算
# ══════════════════════════════════════════════

def compute_icir_weights(
    ic_records: list[dict],
    halflife: int = 60,
    min_records: int = 30,
) -> dict[str, float]:
    """
    ICIR衰减加权：对每个因子，计算衰减加权平均IC，再按比例生成权重。

    w_f = Σ(decay^t × IC_t) 其中 t = recency (最近权重最大)
    decay = 0.5^(1/halflife)
    """
    factor_names = list(FACTOR_DEFS.keys())
    factor_ics: dict[str, list[tuple[int, float]]] = {f: [] for f in factor_names}

    # 按因子分组IC
    for rec in ic_records:
        fn = rec["factor"]
        factor_ics.setdefault(fn, []).append((rec["date"], rec["ic"]))

    weights: dict[str, float] = {}
    decay = 0.5 ** (1.0 / halflife)

    for fn in factor_names:
        ics = factor_ics.get(fn, [])
        if len(ics) < min_records:
            weights[fn] = 0.0
            continue
        # 按日期排序，最近的在最后
        sorted_ics = sorted(ics, key=lambda x: x[0])
        recent_ics = [ic for _, ic in sorted_ics[-min_records:]]

        # 衰减加权平均IC
        total_w = 0.0
        weighted_ic = 0.0
        for i, ic in enumerate(recent_ics):
            w = decay ** (len(recent_ics) - 1 - i)
            weighted_ic += w * ic
            total_w += w
        weights[fn] = weighted_ic / total_w if total_w > 0 else 0.0

    # 正IC归一化
    pos_total = sum(max(0, w) for w in weights.values())
    if pos_total > 0:
        for fn in weights:
            weights[fn] = max(0, weights[fn]) / pos_total
    else:
        # 全部回退到等权
        n_active = sum(1 for w in weights.values() if w > 0)
        if n_active > 0:
            uniform = 1.0 / n_active
            for fn in weights:
                weights[fn] = uniform if weights[fn] > 0 else 0.0

    return weights


# ══════════════════════════════════════════════
# 中性化（市值代理变量：成交额）
# ══════════════════════════════════════════════

def neutralize_by_size(z_scores: np.ndarray, amount: np.ndarray) -> np.ndarray:
    """用量代理变量(amount)做截面中性化：回归残差代替原始值。"""
    valid = ~(np.isnan(z_scores) | np.isinf(z_scores) | (amount <= 0))
    if valid.sum() < 5:
        return z_scores
    x = np.log(amount[valid])
    y = z_scores[valid]
    try:
        slope, intercept = np.polyfit(x, y, 1)
        residuals = np.full_like(z_scores, np.nan)
        residuals[valid] = y - (slope * x + intercept)
        return residuals
    except np.linalg.LinAlgError:
        return z_scores


# ══════════════════════════════════════════════
# 报告输出
# ══════════════════════════════════════════════

def print_ic_report(ic_records: list[dict]) -> None:
    """输出因子IC分析报告。"""
    factor_names = list(FACTOR_DEFS.keys())
    print(f"\n{'='*60}")
    print(f"  因子IC分析报告 ({len(ic_records)} 个观测)")
    print(f"{'='*60}")

    # 各因子汇总
    print(f"\n  ── 各因子IC统计 ──")
    print(f"  {'因子名':<22} {'平均IC':<10} {'IC标准差':<10} {'ICIR':<10} {'正IC占比':<10} {'样本数'}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*6}")

    for fn in factor_names:
        ics = [r["ic"] for r in ic_records if r["factor"] == fn]
        if not ics:
            continue
        arr = np.array(ics)
        mean_ic = float(np.mean(arr))
        std_ic = float(np.std(arr))
        icir = mean_ic / std_ic if std_ic > 1e-8 else 0.0
        pos_ratio = float(np.sum(arr > 0)) / len(arr)
        print(f"  {fn:<22} {mean_ic:>+6.2%}   {std_ic:>5.2%}    {icir:>+5.2f}    {pos_ratio:>6.1%}    {len(ics)}")


def print_neutralize_comparison(
    ic_raw: list[dict],
    ic_neut: list[dict],
) -> None:
    """输出中性化前后IC对比。"""
    factor_names = list(FACTOR_DEFS.keys())
    print(f"\n{'='*60}")
    print(f"  中性化前后IC对比")
    print(f"{'='*60}")
    print(f"  {'因子名':<22} {'原始IC':<10} {'中性化IC':<10} {'变化':<10} {'改善'}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10} {'-'*6}")

    for fn in factor_names:
        raw = [r["ic"] for r in ic_raw if r["factor"] == fn]
        neut = [r["ic"] for r in ic_neut if r["factor"] == fn]
        if not raw or not neut:
            continue
        mean_raw = float(np.mean(raw))
        mean_neut = float(np.mean(neut))
        delta = mean_neut - mean_raw
        improved = "✅" if abs(mean_neut) > abs(mean_raw) else ("❌" if abs(mean_neut) < abs(mean_raw) else "—")
        print(f"  {fn:<22} {mean_raw:>+6.2%}   {mean_neut:>+6.2%}   {delta:>+6.2%}   {improved}")


def print_weight_comparison(
    ic_records: list[dict],
    static_weights: dict[str, float],
    icir_weights: dict[str, float],
) -> None:
    """输出ICIR衰减 vs 静态权重对比。"""
    factor_names = list(FACTOR_DEFS.keys())
    print(f"\n{'='*60}")
    print(f"  ICIR衰减 vs 静态权重对比")
    print(f"{'='*60}")
    print(f"  {'因子名':<22} {'静态权重':<10} {'ICIR权重':<10} {'差异':<10}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10}")

    for fn in factor_names:
        sw = static_weights.get(fn, 0)
        iw = icir_weights.get(fn, 0)
        diff = iw - sw
        print(f"  {fn:<22} {sw:>7.2%}   {iw:>7.2%}   {diff:>+7.2%}")

    # 用ICIR权重回算复合IC
    static_w = np.array([static_weights.get(f, 0) for f in factor_names])
    icir_w = np.array([icir_weights.get(f, 0) for f in factor_names])

    # 按日均IC估算
    mean_ics = []
    for fn in factor_names:
        ics = [r["ic"] for r in ic_records if r["factor"] == fn]
        mean_ics.append(float(np.mean(ics)) if ics else 0.0)
    mean_ics = np.array(mean_ics)

    static_portfolio_ic = float(np.dot(static_w, mean_ics))
    icir_portfolio_ic = float(np.dot(icir_w, mean_ics))

    print(f"\n  ── 组合层面估算 ──")
    print(f"  静态权重组合日均IC: {static_portfolio_ic:+.4f}")
    print(f"  ICIR权重组合日均IC: {icir_portfolio_ic:+.4f}")
    delta = icir_portfolio_ic - static_portfolio_ic
    verdict = "✅ ICIR更优" if delta > 0 else ("❌ 静态更优" if delta < 0 else "— 持平")
    print(f"  差异: {delta:+.4f}  {verdict}")
    print()


# ══════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="因子研究：中性化+ICIR衰减分析")
    parser.add_argument("--ic-only", action="store_true", help="仅IC分析")
    parser.add_argument("--neutralize", action="store_true", help="仅中性化对比")
    parser.add_argument("--icir", action="store_true", help="仅ICIR衰减对比")
    args = parser.parse_args()

    run_all = not (args.ic_only or args.neutralize or args.icir)

    print(f"\n{'#'*60}")
    print(f"  因子研究工具 v1.0")
    print(f"{'#'*60}\n")

    # 1. 加载数据
    print("  [1/4] 加载数据...")
    stocks = load_stock_dates()
    if not stocks:
        print("❌ 数据加载失败")
        return

    # 2. 计算IC
    print("  [2/4] 计算每日因子IC...")
    ic_records = compute_daily_ic(stocks)
    if not ic_records:
        print("❌ IC计算失败（数据不足）")
        return

    if run_all or args.ic_only:
        print_ic_report(ic_records)

    # 3. 中性化对比
    if run_all or args.neutralize:
        print("\n  [3/4] 中性化对比...")
        # 重新计算IC，加入中性化
        ic_neut = compute_daily_ic_neutralized(stocks)
        if ic_neut:
            print_neutralize_comparison(ic_records, ic_neut)

    # 4. ICIR衰减对比
    if run_all or args.icir:
        print("\n  [4/4] ICIR衰减 vs 静态权重...")

        # 计算ICIR权重
        icir_weights = compute_icir_weights(ic_records, halflife=60)

        # 当前静态权重
        static_weights = {fn: fd["weight"] for fn, fd in FACTOR_DEFS.items()}

        print_weight_comparison(ic_records, static_weights, icir_weights)


def compute_daily_ic_neutralized(
    stocks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """带市值中性化的IC计算（与compute_daily_ic逻辑相同，但标准化后做中性化）。"""
    factor_names = list(FACTOR_DEFS.keys())
    all_dates_set: set[str] = set()
    for s in stocks.values():
        all_dates_set.update(s["dates"])
    all_dates = sorted(all_dates_set)

    results: list[dict] = []

    for di in range(1, len(all_dates)):
        date = all_dates[di]

        raw_factors: dict[str, list[float]] = {f: [] for f in factor_names}
        codes: list[str] = []
        forward_rets: list[float] = []
        amounts: list[float] = []

        for code, sd in stocks.items():
            try:
                idx = sd["dates"].index(date)
            except ValueError:
                continue
            if idx < 60:
                continue
            closes = sd["closes"][:idx + 1]
            volumes = sd["volumes"][:idx + 1]
            if idx + 1 >= len(sd["closes"]):
                continue
            fwd_ret = (sd["closes"][idx + 1] - sd["closes"][idx]) / sd["closes"][idx]
            forward_rets.append(float(fwd_ret))
            for fn in factor_names:
                raw_factors[fn].append(FACTOR_FUNCS[fn](closes, volumes))
            codes.append(code)
            amounts.append(float(sd["amounts"][idx]))

        n = len(codes)
        if n < 5:
            continue

        # 对每个因子做中性化后算IC
        for fn in factor_names:
            vals = np.array(raw_factors[fn])
            valid = ~(np.isnan(vals) | np.isinf(vals))
            if valid.sum() < 5:
                continue

            # 标准化
            mean = float(np.mean(vals[valid]))
            std = float(np.std(vals[valid]))
            if std < 1e-8:
                continue
            z = (vals - mean) / std
            z = np.clip(z, -3.0, 3.0)

            # 中性化（用量作为市值代理）
            amt_arr = np.array(amounts)
            z_neut = neutralize_by_size(z, amt_arr)

            # IC
            mask = valid & ~np.isnan(forward_rets) & ~np.isinf(forward_rets)
            if mask.sum() < 5:
                continue
            ic = _spearman_r(z_neut[mask], np.array(forward_rets)[mask])
            if not math.isnan(ic) and not math.isinf(ic):
                results.append({
                    "date": date, "factor": fn,
                    "ic": round(ic, 4), "n": int(mask.sum()),
                })

    print(f"  中性化IC计算完成: {len(results)} 个观测")
    return results


if __name__ == "__main__":
    main()
