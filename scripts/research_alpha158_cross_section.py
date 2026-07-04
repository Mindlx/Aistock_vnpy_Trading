#!/usr/bin/env python3
"""
Alpha158 横截面 IC 验证原型

验证假设: Alpha158 的 58 因子在横截面(跨股票排名)上
的信息系数(IC)是否显著高于时序(单股票涨跌预测)。

用法:
    .venv/bin/python scripts/research_alpha158_cross_section.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from systems.lynx_vnpy.lynx_signal import fetch_daily_bars, STOCK_CODES, STOCK_NAMES
from systems.lynx_vnpy.vnpy_bridge.alpha_predictor import _compute_alpha_factors

# 手动实现横截面归一化(RankZscore)
def _rank_zscore(series: pd.Series) -> pd.Series:
    """Rank-based z-score: rank → uniform → normal"""
    ranked = series.rank(pct=True)
    from scipy.stats import norm
    return ranked.apply(lambda p: norm.ppf(max(0.001, min(0.999, p))))


def main():
    print("=" * 60)
    print("Alpha158 横截面 IC 验证原型")
    print(f"股票池: {len(STOCK_CODES)} 只")
    print("=" * 60)

    # 1. 拉取所有股票数据
    all_data = {}
    for code in STOCK_CODES:
        df = fetch_daily_bars(code, days=180)
        if df is not None and len(df) >= 120:
            # 统一列名(alpha_predictor 需要英文)
            df = df.rename(columns={
                "开盘": "open", "最高": "high", "最低": "low",
                "收盘": "close", "成交量": "volume", "日期": "date",
            })
            df["date"] = pd.to_datetime(df["date"])
            df["code"] = code
            all_data[code] = df
            print(f"  {code} {STOCK_NAMES.get(code, '')}: {len(df)} 行  "
                  f"{df['date'].min().date()} ~ {df['date'].max().date()}")
        else:
            print(f"  {code}: 数据不足")

    # 2. 对齐公共日期
    common_dates = None
    for code, df in all_data.items():
        dates = set(df["date"].dt.date)
        if common_dates is None:
            common_dates = dates
        else:
            common_dates = common_dates & dates
    common_dates = sorted(common_dates)
    print(f"\n公共交易日: {len(common_dates)} 天 ({common_dates[0]} ~ {common_dates[-1]})")

    if len(common_dates) < 30:
        print("公共交易日不足 30 天，无法做出有意义分析")
        return

    # 3. 逐日计算 Alpha158 因子 + 横截面特征
    factor_dfs = {}
    for code in STOCK_CODES:
        if code not in all_data:
            continue
        df = all_data[code].sort_values("date").reset_index(drop=True)
        # 计算 Alpha158 因子
        factors = _compute_alpha_factors(df)
        if factors is None or factors.empty:
            print(f"  {code}: 因子计算失败")
            continue
        # 合并到原 df
        df_with_factors = pd.concat([df, factors], axis=1)
        factor_dfs[code] = df_with_factors

    # 4. 横截面 IC 计算
    factor_cols = [c for c in factor_dfs[list(factor_dfs.keys())[0]].columns
                   if c not in ['open', 'high', 'low', 'close', 'volume', 'date', 'code']]

    cs_ics = {f: [] for f in factor_cols}
    ts_ics = {f: [] for f in factor_cols}

    for i, date in enumerate(common_dates):
        if i >= len(common_dates) - 1:
            break
        next_date = common_dates[i + 1]

        # 收集当天所有股票的因子 + 次日收益
        today_data = []
        for code in STOCK_CODES:
            if code not in factor_dfs:
                continue
            row = factor_dfs[code][factor_dfs[code]["date"].dt.date == date]
            if row.empty:
                continue
            row = row.iloc[-1]

            next_row = all_data[code][all_data[code]["date"].dt.date == next_date]
            if next_row.empty:
                continue
            next_close = next_row.iloc[-1]["close"]
            today_close = row["close"]
            ret = (next_close - today_close) / today_close if today_close > 0 else 0

            entry = {"code": code, "ret": ret}
            for f in factor_cols:
                entry[f] = row.get(f, np.nan)
            today_data.append(entry)

        if len(today_data) < 6:  # 至少 6 只股票才有意义
            continue

        today_df = pd.DataFrame(today_data)

        for f in factor_cols:
            # 横截面 IC: 因子值 vs 收益的秩相关
            factor_vals = pd.to_numeric(today_df[f], errors='coerce')
            ret_vals = pd.to_numeric(today_df["ret"], errors='coerce')
            valid = pd.DataFrame({f: factor_vals, "ret": ret_vals}).dropna()
            if len(valid) >= 6:
                cs_ic = valid[f].corr(valid["ret"], method="spearman")
                cs_ics[f].append(cs_ic if not np.isnan(cs_ic) else 0)
            else:
                cs_ics[f].append(0)

            # 时序 IC: 每个股票独立算, 再平均
            ts_ic_list = []
            for code in STOCK_CODES:
                if code not in factor_dfs or code not in all_data:
                    continue
                series = factor_dfs[code]
                # 找到这个日期附近的数据
                idx = series[series["date"].dt.date <= date].index
                if len(idx) < 30:
                    continue
                series_window = series.loc[idx[-30]:].copy()
                if len(series_window) < 10:
                    continue

                # 计算因子值的变化 vs 收益
                factor_vals = pd.to_numeric(series_window[f], errors='coerce').values
                rets = pd.to_numeric(series_window["close"].pct_change().shift(-1), errors='coerce').values
                valid_mask = ~(np.isnan(factor_vals) | np.isnan(rets))
                if valid_mask.sum() >= 6:
                    corr = pd.Series(factor_vals[valid_mask]).corr(
                        pd.Series(rets[valid_mask]), method="spearman")
                    if not np.isnan(corr):
                        ts_ic_list.append(corr)
            ts_ics[f].append(np.mean(ts_ic_list) if ts_ic_list else 0)

    # 5. 汇总
    print(f"\n{'='*60}")
    print("58 因子横截面 IC vs 时序 IC 对比")
    print(f"{'='*60}")
    print(f"{'因子名':20s} {'CS IC':>8s} {'TS IC':>8s} {'差异':>8s} {'方向':>6s}")
    print("-" * 55)

    results = []
    for f in factor_cols:
        cs = np.mean(cs_ics[f]) if cs_ics[f] else 0
        ts_val = np.mean(ts_ics[f]) if ts_ics[f] else 0
        diff = cs - ts_val
        direction = "🟢" if cs > 0.03 else ("🔴" if cs < -0.03 else "➡️")
        results.append((f, cs, ts_val, diff, direction))

    results.sort(key=lambda x: -abs(x[1]))
    top_n = 15
    for f, cs, ts_val, diff, direction in results[:top_n]:
        print(f"{f:20s} {cs:>+8.4f} {ts_val:>+8.4f} {diff:>+8.4f} {direction:>6s}")

    print(f"\n... 共 {len(results)} 个因子, 显示前 {top_n}")

    # 6. 整体统计
    cs_all = np.mean([r[1] for r in results])
    ts_all = np.mean([r[2] for r in results])
    cs_abs = np.mean([abs(r[1]) for r in results])
    ts_abs_val = np.mean([abs(r[2]) for r in results])
    cs_pos = sum(1 for r in results if r[1] > 0)
    ts_pos = sum(1 for r in results if r[2] > 0)

    print(f"\n{'='*60}")
    print("整体统计")
    print(f"{'='*60}")
    print(f"  横截面平均 IC: {cs_all:+.4f}  (正值: {cs_pos}/{len(results)})")
    print(f"  时序平均 IC:   {ts_all:+.4f}  (正值: {ts_pos}/{len(results)})")
    print(f"  横截面 |IC|:   {cs_abs:.4f}")
    print(f"  时序 |IC|:     {ts_abs_val:.4f}")
    print(f"  横截面 vs 时序 提升: {(cs_abs - ts_abs_val):+.4f}")
    print()
    if cs_abs > ts_abs_val * 1.5:
        print("结论: ✅ 横截面 IC 显著优于时序 IC, 值得开发")
    elif cs_abs > ts_abs_val:
        print("结论: 🟡 横截面 IC 略优于时序 IC, 有价值但有限")
    else:
        print("结论: ❌ 横截面 IC 未优于时序 IC, 当前时序用法已足够")


if __name__ == "__main__":
    main()
