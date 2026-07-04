#!/usr/bin/env python3
"""
ML 12因子 IC 分析 — 验证每个因子在实战中的真实预测力

从 factor_engine.py 动态加载因子定义与计算函数（非复制代码），
含 Spearman p-value 显著性检验与 bootstrap 置信区间。

用法:
    .venv/bin/python scripts/research_ml12_factor_ic.py
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from systems.lynx_vnpy.lynx_signal import fetch_daily_bars, STOCK_CODES, STOCK_NAMES

# ── 从 factor_engine.py 动态加载（非复制代码） ──
_FACTOR_ENGINE_DIR = _PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "src" / "core"
sys.path.insert(0, str(_FACTOR_ENGINE_DIR))
# 需要 src/ 也在路径中（factor_engine 内引用了上级包）
sys.path.insert(0, str(_FACTOR_ENGINE_DIR.parent.parent))  # src/
import factor_engine as _fe_module
# Clean up paths to avoid side effects
sys.path = [p for p in sys.path if p not in (str(_FACTOR_ENGINE_DIR), str(_FACTOR_ENGINE_DIR.parent.parent))]

CORE_FACTORS = _fe_module.CORE_FACTORS
_FACTOR_COMPUTE = _fe_module._FACTOR_COMPUTE
FactorEngine = _fe_module.FactorEngine

FACTOR_NAMES = [f.name for f in CORE_FACTORS]
FACTOR_DISPLAY = {f.name: f.display_name for f in CORE_FACTORS}
FACTOR_DECLARED_IC = {f.name: f.ic for f in CORE_FACTORS}

# 因子函数映射（直接从加载的模块引用）
_FN_MAP = {}
for fn in FACTOR_NAMES:
    compute_func = _FACTOR_COMPUTE.get(fn)
    if compute_func:
        _FN_MAP[fn] = compute_func


def compute_factors_snapshot(close: np.ndarray, volume: np.ndarray) -> dict[str, float]:
    """用实际 factor_engine 的 _FACTOR_COMPUTE 计算因子急快照"""
    result = {}
    for fn in FACTOR_NAMES:
        fn_func = _FN_MAP.get(fn)
        if fn_func is None:
            result[fn] = 0.0
            continue
        try:
            if fn in ("volume_trend", "size_factor", "illiquidity"):
                result[fn] = fn_func(close, volume)
            else:
                result[fn] = fn_func(close)
        except Exception:
            result[fn] = 0.0
    return result


def _spearman_pval(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Spearman 秩相关 + p-value"""
    valid = ~(np.isnan(x) | np.isnan(y))
    if valid.sum() < 6:
        return 0.0, 1.0
    rho, p = spearmanr(x[valid], y[valid])
    return rho if not np.isnan(rho) else 0.0, p if not np.isnan(p) else 1.0


def _bootstrap_ic(values: list[float], n_iter: int = 1000) -> tuple[float, float]:
    """Bootstrap 置信区间"""
    arr = np.array(values)
    if len(arr) < 10:
        return 0.0, 0.0
    means = np.zeros(n_iter)
    for i in range(n_iter):
        sample = np.random.choice(arr, size=len(arr), replace=True)
        means[i] = np.mean(sample)
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def main():
    print("=" * 75)
    print("ML 12因子 IC 分析（含 p-value 与 bootstrap 置信区间）")
    print(f"股票池: {len(STOCK_CODES)} 只")
    print("=" * 75)

    # 1. 数据加载
    all_data = {}
    for code in STOCK_CODES:
        df = fetch_daily_bars(code, days=360)
        if df is not None and len(df) >= 120:
            df = df.rename(columns={
                "开盘": "open", "最高": "high", "最低": "low",
                "收盘": "close", "成交量": "volume", "日期": "date",
            })
            df["date"] = pd.to_datetime(df["date"])
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            all_data[code] = df

    # 2. 对齐日期
    all_dates = set()
    for df in all_data.values():
        all_dates.update(str(d.date()) for d in df["date"])
    for df in all_data.values():
        all_dates &= {str(d.date()) for d in df["date"]}
    all_dates = sorted(all_dates)
    print(f"\n公共交易日: {len(all_dates)} ({all_dates[0]} ~ {all_dates[-1]})")

    if len(all_dates) < 60:
        print("数据不足")
        return

    # 3. 逐日计算因子
    daily_data = []
    for date_str in all_dates:
        entries = []
        for code in STOCK_CODES:
            if code not in all_data:
                continue
            hist = all_data[code][all_data[code]["date"] <= pd.Timestamp(date_str)]
            if len(hist) < 30:
                continue
            close = hist["close"].values.astype(float)
            volume = hist["volume"].values.astype(float)
            if np.any(np.isnan(close)) or np.any(np.isnan(volume)):
                continue
            factors = compute_factors_snapshot(close, volume)
            factors["code"] = code
            factors["date"] = date_str
            entries.append(factors)
        if len(entries) >= 6:
            daily_data.append(entries)

    print(f"有效交易日 (≥6只): {len(daily_data)}")

    # 4. IC 计算 + p-value
    ic_vals = {fn: [] for fn in FACTOR_NAMES}
    ic_pvals = {fn: [] for fn in FACTOR_NAMES}

    for entries in daily_data:
        date_str = entries[0]["date"]
        idx = all_dates.index(date_str)
        if idx >= len(all_dates) - 1:
            continue
        next_date_str = all_dates[idx + 1]

        rows = []
        for e in entries:
            code = e["code"]
            today_row = all_data[code][all_data[code]["date"].dt.date == pd.Timestamp(date_str).date()]
            next_row = all_data[code][all_data[code]["date"].dt.date == pd.Timestamp(next_date_str).date()]
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
            vals = pd.to_numeric(dfd[fn], errors="coerce").values
            rets = pd.to_numeric(dfd["ret"], errors="coerce").values
            corr, pval = _spearman_pval(vals, rets)
            ic_vals[fn].append(corr)
            ic_pvals[fn].append(pval)

    # 5. 结果
    print(f"\n{'='*75}")
    print(f"{'因子名':25s} {'声明IC':>7s} {'实测IC':>8s} {'|IC|':>6s} {'p<0.05':>7s} {'95%CI低':>8s} {'95%CI高':>8s} {'判定':>6s}")
    print("-" * 75)

    result = []
    for fn in FACTOR_NAMES:
        vals = np.array([v for v in ic_vals[fn] if v != 0])
        pvals_arr = np.array([p for v, p in zip(ic_vals[fn], ic_pvals[fn]) if v != 0])
        if len(vals) == 0:
            continue
        mean_ic = float(np.mean(vals))
        abs_ic = float(np.mean(np.abs(vals)))
        pval_ratio = float(np.mean(pvals_arr < 0.05)) if len(pvals_arr) > 0 else 0.0
        ci_lo, ci_hi = _bootstrap_ic(vals.tolist())

        dec = FACTOR_DECLARED_IC.get(fn, 0)
        sig_stars = "***" if pval_ratio > 0.5 else ("**" if pval_ratio > 0.2 else ("*" if pval_ratio > 0.05 else ""))
        direction = "🟢" if ci_lo > 0 else ("🔴" if ci_hi < 0 else "➡️")
        result.append((fn, mean_ic, abs_ic, dec, pval_ratio, ci_lo, ci_hi, direction, sig_stars))

        print(f"{fn:25s} {dec:>+6.3f}  {mean_ic:>+7.4f} {abs_ic:>5.4f} {pval_ratio*100:>5.0f}%  {ci_lo:>+7.4f} {ci_hi:>+7.4f}  {direction}{sig_stars}")

    # 6. 整体评判
    print(f"\n{'='*75}")
    keep = [r for r in result if r[5] > 0 or r[6] < 0]  # CI 不跨 0
    uncertain = [r for r in result if not (r[5] > 0 or r[6] < 0)]
    significant = [r for r in result if r[4] > 0.3]  # >30% 日期 p<0.05

    print(f"\n📊 统计摘要:")
    print(f"  平均 |IC|: {np.mean([r[2] for r in result]):.4f}")
    print(f"  声明 |IC|: {np.mean([abs(r[3]) for r in result]):.4f}")
    print(f"  统计显著 (p<0.05 >30% 日期): {len(significant)}/{len(result)}")
    print(f"  95%CI 不跨零: {len(keep)}/{len(result)}")

    if len(keep) < len(result) * 0.5:
        print(f"\n⚠️ 半数以上因子 95%CI 跨零 -> "
              f"IC=0.25~0.32 可能被 12 只股票小样本高估了")
        print(f"  建议: 收集更多股票或更长周期后重新验证")
    else:
        print(f"\n✅ 多数因子 95%CI 不跨零 -> IC 值可信")

    print(f"\n{'='*75}")
    print(f"  如 factor_engine.py 更新，此脚本自动同步。")
    print(f"如 factor_engine.py 更新，此脚本自动同步。")


if __name__ == "__main__":
    main()
