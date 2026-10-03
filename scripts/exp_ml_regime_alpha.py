#!/usr/bin/env python3
"""exp_ml_regime_alpha — ML 方向信号 regime 分层 alpha/beta 审计.

目的 (c1skill 2026-10-03 专项论证):
    判断 MindLynx 的"高准确率"(2026Q2 ~67%) 是 alpha 还是市场 beta,
    并回答"是否应据 c1test 报告调整 ML 融合权重"。

核心方法 (对应 c1skill Stage 2/4 + 铁律#11 多方法交叉):
    1. 多持有期: T+1 / T+5 / T+20
    2. 多时段: 按季度 / 月分层 (非仅最近 90 天窗口)
    3. regime 分层: 按分析时点之前 20 交易日市场趋势 (ex-ante, 无前视)
    4. 同状态基准 (D6a), 全部在"有方向"子集上:
       - 恒定看多 = 上涨率; 恒定看空 = 下跌率
       - 同多空比随机 = long_share*up + short_share*down (beta+方向偏好双校正)
       ML 需超越"同多空比随机"才代表有方向选择能力 (非单纯偏空)
    5. 多空条件准确率: 看多命中率 vs 看空命中率 (检验 scoring asymmetry)
    6. 采样独立性: 独立分析日数 + T+5/T+20 非重叠时点数
    7. 黄金参考: T+1 结果与生产 c1test 的 calendar-day join 口径逐点核对

口径声明 (铁律#12):
    - 持有期语义 = 交易日; 用 stock_daily 排序确定 T+N (修正生产 calendar-day join)
    - 前向收益 = 未来 N 个交易日 pct_chg 累积复利 (pct_chg 单位=%)
    - ML 方向 = fusion_equivalent 口径 (norm_v5(score)*0.8 -> _sign(0.1)), v5.0=生产映射
    - 仅取 ML 子系统行 report_type IN ('full','simple'), 排除 'fusion'(融合管线自身写回)/'MARKET'
    - regime = 分析日之前 20 交易日等权市场收益 (ex-ante, 无前视)
    - 中性 (_sign==0) 不计入准确率分母, 单独报告
    - 去重 (默认 --dedup latest): 生产 fusion 口径 = 每股票取最新一条
      (services/ml_factor_service.py:138-142 ORDER BY created_at DESC LIMIT 1);
      不dedup 会因盘中重复分析 (同 code/day 可达 29 条, 共享同一 T+1 收益)
      虚增样本并高估准确率

输出: /tmp/opencode/ml_regime_alpha_<date>.json + 终端摘要
"""
from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ML_DB = REPO / "systems/MindLynx-Aistock/data/stock_analysis.db"
OUT_DIR = Path("/tmp/opencode")

HORIZONS = (1, 5, 20)
REGIME_WINDOW = 20          # ex-ante 市场趋势回看窗口 (交易日)
REGIME_THRESH = 0.02        # 20 日累计 +/-2% 划分 down/flat/up

# 桶: [cor, tot, neu, pos_eval, n_all, longs, neg_eval]
B_IDX = {"cor": 0, "tot": 1, "neu": 2, "pos": 3, "n": 4, "longs": 5, "neg": 6}


def assert_design() -> None:
    assert HORIZONS == (1, 5, 20), "必须覆盖 T+1/5/20"
    assert REGIME_WINDOW > 0 and REGIME_THRESH > 0
    assert ML_DB.exists(), f"ML DB 缺失: {ML_DB}"
    print("[assert_design] OK: 多持有期/多时段/regime/三基准/独立性/黄金参考 均在设计中")


def norm_v5(score: int) -> float:
    """生产 ML L7 映射 (v5.0), 等价 src/normalizer.py::normalize_mindlynx_score."""
    if 49 < score < 52:
        return 0.0
    if score <= 49:
        if score <= 19:
            return -2.5
        if score <= 39:
            return -1.5
        return -2.0
    if score >= 80:
        return 1.5
    if score >= 60:
        return 1.0
    return 0.5


def _sign(x: float, t: float = 0.1) -> int:
    if x > t:
        return 1
    if x < -t:
        return -1
    return 0


def ml_dir(score: int) -> int:
    return _sign(norm_v5(score) * 0.8)


def new_bucket():
    return [0, 0, 0, 0, 0, 0, 0]


def update(arr, pred, actual):
    """更新桶: pred 为 ML 方向 (0=中性跳过), actual in {-1,0,1}."""
    arr[B_IDX["n"]] += 1
    if pred == 0:
        arr[B_IDX["neu"]] += 1
        return
    arr[B_IDX["tot"]] += 1
    if actual == 1:
        arr[B_IDX["pos"]] += 1
    elif actual == -1:
        arr[B_IDX["neg"]] += 1
    if pred == 1:
        arr[B_IDX["longs"]] += 1
    if pred == actual:
        arr[B_IDX["cor"]] += 1


def fmt(arr):
    cor, tot, neu, pos, n, longs, neg = arr
    ml_acc = round(cor / tot * 100, 1) if tot else 0.0
    const_long = round(pos / tot * 100, 1) if tot else 0.0
    const_short = round(neg / tot * 100, 1) if tot else 0.0
    lshare = longs / tot if tot else 0.0
    mix = round((lshare * const_long + (1 - lshare) * const_short), 1) if tot else 0.0
    return {
        "ml_acc": ml_acc, "correct": cor, "total": tot, "neutral_excluded": neu, "n": n,
        "const_long_base": const_long, "const_short_base": const_short, "const_mix_base": mix,
        "ml_minus_mix": round(ml_acc - mix, 1) if tot else None,
        "ml_minus_const_short": round(ml_acc - const_short, 1) if tot else None,
        "ml_long_share": round(lshare * 100, 1) if tot else 0.0,
    }


def load_data():
    conn = sqlite3.connect(str(ML_DB))
    daily = defaultdict(list)
    for code, d, p, cl in conn.execute(
        "SELECT code, date, pct_chg, close FROM stock_daily WHERE pct_chg IS NOT NULL"
    ):
        daily[code].append((d, p, cl))
    for code in daily:
        daily[code].sort()
    mkt_by_day = defaultdict(list)
    for code, rows in daily.items():
        for d, p, _cl in rows:
            mkt_by_day[d].append(p)
    analyses = conn.execute(
        """SELECT code, substr(created_at,1,10) AS d, sentiment_score, created_at
           FROM analysis_history
           WHERE sentiment_score IS NOT NULL
             AND report_type IN ('full', 'simple')"""  # 仅 ML 子系统行; 排除 'fusion'(融合自身)/MARKET
    ).fetchall()
    conn.close()
    return daily, mkt_by_day, analyses


def dedup_analyses(analyses, mode):
    """生产口径: services/ml_factor_service.py 每股票取最新一条 (ORDER BY created_at DESC LIMIT 1).

    同一 (code, day) 存在多条盘中重复分析 (可达 29 条, 共享同一 T+1 收益),
    不dedup 会把 n 虚增约 3 倍并高估准确率。mode: latest|first|none。
    """
    if mode == "none":
        return [(c, d, s) for c, d, s, _t in analyses]
    pick = {}
    for c, d, s, t in analyses:
        key = (c, d)
        if key not in pick or (mode == "latest" and t > pick[key][0]) or (mode == "first" and t < pick[key][0]):
            pick[key] = (t, s)
    return [(c, d, s) for (c, d), (_t, s) in pick.items()]


def build_market_series(mkt_by_day):
    days = sorted(mkt_by_day)
    mkt_daily = [sum(mkt_by_day[d]) / len(mkt_by_day[d]) for d in days]
    return days, mkt_daily


def forward_return(daily_rows, dates_list, d, n):
    """未来第 n 个交易日的 N 日累积复利 (pct_chg 路径)."""
    i = bisect.bisect_right(dates_list, d)
    if i + n - 1 >= len(daily_rows):
        return None
    cum = 1.0
    for j in range(i, i + n):
        cum *= (1.0 + daily_rows[j][1] / 100.0)
    return (cum - 1.0) * 100.0


def forward_return_close(daily_rows, dates_list, d, n):
    """独立参考路径: close 价格比值 (校验 pct_chg 累积链). 需 i-1>=0."""
    i = bisect.bisect_right(dates_list, d)
    if i - 1 < 0 or i + n - 1 >= len(daily_rows):
        return None
    return (daily_rows[i + n - 1][2] / daily_rows[i - 1][2] - 1.0) * 100.0


def prior_market_ret(mkt_daily, days, d, window):
    i = bisect.bisect_left(days, d)
    if i < window:
        return None
    cum = 1.0
    for r in mkt_daily[i - window:i]:
        cum *= (1.0 + r / 100.0)
    return (cum - 1.0) * 100.0


def regime_of(r):
    if r is None:
        return None
    if r < -REGIME_THRESH * 100:
        return "down"
    if r > REGIME_THRESH * 100:
        return "up"
    return "flat"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dedup", choices=["latest", "first", "none"], default="latest",
                    help="每(code,day)保留口径; latest=生产对齐 (默认), none=含盘中重复")
    args = ap.parse_args()

    assert_design()
    daily, mkt_by_day, raw = load_data()
    analyses = dedup_analyses(raw, args.dedup)
    days, mkt_daily = build_market_series(mkt_by_day)
    daily_dates = {c: [r[0] for r in rows] for c, rows in daily.items()}

    overall = {h: new_bucket() for h in HORIZONS}
    by_q, by_m, by_regime = {}, {}, {}
    long_hit = {h: [0, 0] for h in HORIZONS}
    short_hit = {h: [0, 0] for h in HORIZONS}
    golden_ok = golden_checked = 0          # 硬门禁: T+1 生产口径 (calendar next-day join)
    golden_close = {h: [0, 0] for h in HORIZONS}  # 参考: close 路径 (含 0.4%/日 数据不一致)
    kept_dates = set()
    n_rows = 0

    for code, d, score in analyses:
        rows = daily.get(code)
        if not rows:
            continue
        n_rows += 1
        pred = ml_dir(score)
        mkt_r = prior_market_ret(mkt_daily, days, d, REGIME_WINDOW)
        reg = regime_of(mkt_r)
        q = "%sQ%d" % (d[:4], (int(d[5:7]) - 1) // 3 + 1)
        mo = d[:7]
        kept_dates.add(d)
        for h in HORIZONS:
            fr = forward_return(rows, daily_dates[code], d, h)
            if fr is None:
                continue
            actual = 1 if fr > 0 else (-1 if fr < 0 else 0)
            if h == 1:
                nxt = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                j = bisect.bisect_left(daily_dates[code], nxt)
                if j < len(rows) and rows[j][0] == nxt:  # 生产 c1test 会匹配的样本
                    golden_checked += 1
                    if abs(rows[j][1] - fr) < 1e-9:
                        golden_ok += 1
            ref = forward_return_close(rows, daily_dates[code], d, h)
            if ref is not None:
                golden_close[h][0] += 1
                if abs(fr - ref) < 0.05:
                    golden_close[h][1] += 1
            update(overall[h], pred, actual)
            for store, key in ((by_q, q), (by_m, mo), (by_regime, reg)):
                if key is None:
                    continue
                update(store.setdefault(key, {}).setdefault(h, new_bucket()), pred, actual)
            if pred == 1:
                long_hit[h][1] += 1
                if pred == actual:
                    long_hit[h][0] += 1
            elif pred == -1:
                short_hit[h][1] += 1
                if pred == actual:
                    short_hit[h][0] += 1

    indep = {}
    for h in HORIZONS:
        need = 28 if h == 20 else (7 if h == 5 else 1)
        sd = sorted(kept_dates)
        cnt, last = 0, None
        for x in sd:
            xx = _date.fromisoformat(x)
            if last is None or (xx - last).days >= need:
                cnt += 1
                last = xx
        indep["T+%d" % h] = {"distinct_dates": len(sd), "nonoverlap_dates": cnt}

    out = {
        "meta": {
            "horizons": list(HORIZONS), "regime_window": REGIME_WINDOW,
            "regime_thresh_pct": REGIME_THRESH * 100, "n_analyses": n_rows,
            "dedup": args.dedup, "n_raw": len(raw),
            "date_range": [min(kept_dates), max(kept_dates)] if kept_dates else None,
        },
        "golden_ref_T1_production": {"checked": golden_checked, "ok": golden_ok,
                                     "all_match": golden_checked > 0 and golden_ok == golden_checked},
        "golden_ref_close_advisory": {("T+%d" % h): {"checked": golden_close[h][0], "ok": golden_close[h][1]}
                                      for h in HORIZONS},
        "independence": indep,
        "overall": {("T+%d" % h): fmt(overall[h]) for h in HORIZONS},
        "by_quarter": {q: {("T+%d" % h): fmt(v) for h, v in sorted(dd.items())}
                       for q, dd in sorted(by_q.items())},
        "by_month": {m: {("T+%d" % h): fmt(v) for h, v in sorted(dd.items())}
                     for m, dd in sorted(by_m.items())},
        "by_regime": {r: {("T+%d" % h): fmt(v) for h, v in sorted(dd.items())}
                      for r, dd in sorted(by_regime.items())},
        "conditional": {
            "long_hit": {("T+%d" % h): (round(long_hit[h][0] / long_hit[h][1] * 100, 1) if long_hit[h][1] else None) for h in HORIZONS},
            "short_hit": {("T+%d" % h): (round(short_hit[h][0] / short_hit[h][1] * 100, 1) if short_hit[h][1] else None) for h in HORIZONS},
            "long_n": {("T+%d" % h): long_hit[h][1] for h in HORIZONS},
            "short_n": {("T+%d" % h): short_hit[h][1] for h in HORIZONS},
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fpath = OUT_DIR / ("ml_regime_alpha_%s.json" % _date.today().isoformat())
    fpath.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    bar = "=" * 92
    print(bar)
    print("ML 方向信号 regime 分层审计  (fusion_equivalent 口径, 基准在'有方向'子集上)")
    print(bar)
    print("分析日 %d 个 | 区间 %s..%s | dedup=%s (raw=%d -> %d)" % (
        len(kept_dates), *out["meta"]["date_range"], args.dedup, out["meta"]["n_raw"], n_rows))
    print("黄金参考[T+1 生产口径, 硬门禁]: checked=%d ok=%d all_match=%s" % (
        golden_checked, golden_ok, out["golden_ref_T1_production"]["all_match"]))
    print("参考[close 路径, 仅提示]: %s : checked=%d ok=%d" % (
        json.dumps(out["golden_ref_close_advisory"], ensure_ascii=False),
        sum(v["checked"] for v in out["golden_ref_close_advisory"].values()),
        sum(v["ok"] for v in out["golden_ref_close_advisory"].values())))
    print("独立性:", json.dumps(indep, ensure_ascii=False))
    print("\n[总体]  (看多基准=上涨率 / 看空基准=下跌率 / 混比基准=同多空比的随机)")
    for h in ("T+1", "T+5", "T+20"):
        v = out["overall"][h]
        print("  %-4s ML=%5.1f%% (%d/%d) 中性=%d | 看多基准=%5.1f 看空基准=%5.1f 混比基准=%5.1f | ML-混比=%s pp | 看多占比=%s%%" % (
            h, v["ml_acc"], v["correct"], v["total"], v["neutral_excluded"],
            v["const_long_base"], v["const_short_base"], v["const_mix_base"],
            v["ml_minus_mix"], v["ml_long_share"]))
    print("\n[按季度]")
    for q, dd in out["by_quarter"].items():
        parts = []
        for h in ("T+1", "T+5", "T+20"):
            v = dd.get(h)
            if v:
                parts.append("%s ML=%5.1f%% (n=%d) 混比基准=%5.1f%% Δ=%s 看多=%s%%" % (
                    h, v["ml_acc"], v["total"], v["const_mix_base"], v["ml_minus_mix"], v["ml_long_share"]))
        print("  %s | %s" % (q, "  ||  ".join(parts)))
    print("\n[按 regime (分析日前20交易日市场趋势)]")
    for r, dd in out["by_regime"].items():
        parts = []
        for h in ("T+1", "T+5", "T+20"):
            v = dd.get(h)
            if v:
                parts.append("%s ML=%5.1f%% (n=%d) 混比基准=%5.1f%% Δ=%s 看多=%s%%" % (
                    h, v["ml_acc"], v["total"], v["const_mix_base"], v["ml_minus_mix"], v["ml_long_share"]))
        print("  %-5s | %s" % (r, "  ||  ".join(parts)))
    print("\n[多空条件准确率]")
    for h in ("T+1", "T+5", "T+20"):
        print("  %-4s 看多命中 %s%% (n=%d) | 看空命中 %s%% (n=%d)" % (
            h, out["conditional"]["long_hit"][h], out["conditional"]["long_n"][h],
            out["conditional"]["short_hit"][h], out["conditional"]["short_n"][h]))
    print("\nJSON -> %s" % fpath)
    if not out["golden_ref_T1_production"]["all_match"]:
        print("!! 黄金参考(T+1 生产口径)未过, 结果不可信", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
