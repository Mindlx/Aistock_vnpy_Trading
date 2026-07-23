#!/usr/bin/env python3
"""
筹码 × 换手率横截面分析

从 data_warehouse.db 读取所有股票的筹码分布快照和近期日K线，
分析筹码集中度与换手率的组合排名与近期表现的关系。

分组定义:
    Group A: 高度集中(conc<15%) + 低换手(turn<30%分位)  — 锁定筹码, 蓄势待发
    Group B: 高度集中(conc<15%) + 高换手(turn>70%分位)  — 吸筹 or 出货?
    Group C: 较分散(conc>25%)    + 低换手(turn<30%分位)  — 无人问津
    Group D: 较分散(conc>25%)    + 高换手(turn>70%分位)  — 危险区域
    Group E: 其余 (中等区域)

用法:
    python scripts/research_cross_sectional.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "data_warehouse.db"


def load_chip_distribution(conn: sqlite3.Connection) -> dict[str, dict]:
    """加载所有股票最新的筹码分布快照 (concentration, profit_ratio, avg_cost)"""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT stock_code, concentration, profit_ratio, avg_cost, date FROM chip_distribution")
    rows = cur.fetchall()
    # 取每个 stock_code 的最新一条 (按 date 降序, 取第一条)
    latest: dict[str, dict] = {}
    for row in rows:
        code = row["stock_code"]
        if code not in latest or row["date"] > latest[code]["date"]:
            latest[code] = {
                "concentration": float(row["concentration"]),
                "profit_ratio": float(row["profit_ratio"]),
                "avg_cost": float(row["avg_cost"]),
                "date": row["date"],
            }
    return latest


def load_ohlcv(conn: sqlite3.Connection, code: str, n: int = 60) -> list[dict]:
    """加载某股票最近的 n 行 OHLCV 数据 (按 date 升序返回)"""
    cur = conn.cursor()
    cur.execute(
        "SELECT date, close, turnover FROM daily_ohlcv "
        "WHERE stock_code = ? ORDER BY date DESC LIMIT ?",
        (code, n),
    )
    rows = cur.fetchall()
    # 反转回升序
    return [{"date": r[0], "close": float(r[1]), "turnover": float(r[2])} for r in reversed(rows)]


def compute_metrics(code: str, chip: dict, bars: list[dict]) -> dict:
    """计算单只股票的横截面特征和回报指标"""
    conc = chip["concentration"]
    profit = chip["profit_ratio"]
    avg_cost = chip["avg_cost"]

    closes = [b["close"] for b in bars]
    turnovers = [b["turnover"] for b in bars]
    latest_close = closes[-1]

    # 近5日平均换手率
    turn_5d = sum(turnovers[-5:]) / 5 if len(turnovers) >= 5 else sum(turnovers) / len(turnovers)
    # 近5日平均换手率在全部60日中的分位数
    n_bars = len(turnovers)
    count_below = sum(1 for t in turnovers if t < turn_5d)
    turn_pctl = count_below / n_bars if n_bars > 0 else 0.5

    # 成本偏离度
    cost_dev = abs(latest_close - avg_cost) / avg_cost if avg_cost > 0 else 0.0

    # 回报计算
    ret_5d = (closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0
    ret_10d = (closes[-1] / closes[-11] - 1) if len(closes) >= 11 else 0.0
    ret_20d = (closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0

    return {
        "code": code,
        "conc": conc,
        "turn_pctl": turn_pctl,
        "turn_5d": turn_5d,
        "profit": profit,
        "cost_dev": cost_dev,
        "latest_close": latest_close,
        "avg_cost": avg_cost,
        "ret_5d": ret_5d,
        "ret_10d": ret_10d,
        "ret_20d": ret_20d,
    }


def classify_group(conc: float, turn_pctl: float) -> str:
    """按筹码集中度 × 换手率分位划分组别"""
    high_conc = conc < 0.15   # 高度集中
    low_turn = turn_pctl < 0.30
    high_turn = turn_pctl > 0.70

    if high_conc and low_turn:
        return "A"
    elif high_conc and high_turn:
        return "B"
    elif (not high_conc) and conc > 0.25 and low_turn:
        return "C"
    elif (not high_conc) and conc > 0.25 and high_turn:
        return "D"
    else:
        return "E"


def main():
    if not DB_PATH.exists():
        print(f"错误: 数据库不存在 {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))

    # ── 1. 加载筹码分布 ──────────────────────────────────
    chips = load_chip_distribution(conn)
    print("=" * 78)
    print("筹码 × 换手率横截面分析")
    print(f"数据日期: {list(chips.values())[0]['date'] if chips else 'N/A'}")
    print(f"股票数量: {len(chips)}")
    print("=" * 78)

    # ── 2. 加载 OHLCV 并计算指标 ──────────────────────────
    all_metrics = []
    for code in sorted(chips):
        bars = load_ohlcv(conn, code, n=60)
        if len(bars) < 6:
            print(f"  ⚠ {code}: 日K数据不足 ({len(bars)} 行), 跳过")
            continue
        m = compute_metrics(code, chips[code], bars)
        m["group"] = classify_group(m["conc"], m["turn_pctl"])
        all_metrics.append(m)

    conn.close()

    if not all_metrics:
        print("无有效数据")
        return

    # ── 3. 集中度排名 (越小越靠前 = 筹码越集中) ────────────
    sorted_by_conc = sorted(all_metrics, key=lambda x: x["conc"])
    for rank, m in enumerate(sorted_by_conc, 1):
        m["conc_rank"] = rank

    # ── 4. 打印全表 ───────────────────────────────────────
    group_labels = {
        "A": "A:集中+低换",
        "B": "B:集中+高换",
        "C": "C:分散+低换",
        "D": "D:分散+高换",
        "E": "E:中等",
    }

    print()
    hdr = (
        f"{'Code':<8} {'Grp':<4} {'Rank':>4} "
        f"{'Conc%':>6} {'Turn5d%':>7} {'TurnPctl':>7} "
        f"{'Profit%':>7} {'CostDev':>7} "
        f"{'Ret5d':>7} {'Ret10d':>7} {'Ret20d':>7} "
        f"{'Close':>8} {'AvgCost':>8}"
    )
    print(hdr)
    print("-" * len(hdr))

    # 按组别排序, 组内按集中度排序
    for m in sorted(all_metrics, key=lambda x: (x["group"], x["conc"])):
        line = (
            f"{m['code']:<8} {m['group']:<4} {m['conc_rank']:>4} "
            f"{m['conc']*100:>6.1f}% {m['turn_5d']:>7.2f} {m['turn_pctl']*100:>6.0f}% "
            f"{m['profit']*100:>6.1f}% {m['cost_dev']:>6.1%} "
            f"{m['ret_5d']:>+6.1%} {m['ret_10d']:>+6.1%} {m['ret_20d']:>+6.1%} "
            f"{m['latest_close']:>8.2f} {m['avg_cost']:>8.2f}"
        )
        print(line)

    # ── 5. 各组统计 ───────────────────────────────────────
    print()
    print("=" * 78)
    print("各组平均回报对比")
    print("=" * 78)

    groups: dict[str, list[dict]] = {}
    for m in all_metrics:
        groups.setdefault(m["group"], []).append(m)

    stats_hdr = (
        f"{'Group':<14} {'Count':>5} "
        f"{'Conc%':>7} {'TurnPctl':>8} "
        f"{'Profit%':>7} {'CostDev':>7} "
        f"{'AvgRet5d':>8} {'AvgRet10d':>9} {'AvgRet20d':>9}"
    )
    print(stats_hdr)
    print("-" * len(stats_hdr))

    for g in ["A", "B", "C", "D", "E"]:
        items = groups.get(g, [])
        if not items:
            print(f"{group_labels.get(g, g):<14}     0")
            continue
        n = len(items)
        avg_conc = sum(m["conc"] for m in items) / n
        avg_turn_pctl = sum(m["turn_pctl"] for m in items) / n
        avg_profit = sum(m["profit"] for m in items) / n
        avg_cost_dev = sum(m["cost_dev"] for m in items) / n
        avg_ret5d = sum(m["ret_5d"] for m in items) / n
        avg_ret10d = sum(m["ret_10d"] for m in items) / n
        avg_ret20d = sum(m["ret_20d"] for m in items) / n

        # 计算标准差
        std_ret5d = (sum((m["ret_5d"] - avg_ret5d) ** 2 for m in items) / n) ** 0.5
        std_ret20d = (sum((m["ret_20d"] - avg_ret20d) ** 2 for m in items) / n) ** 0.5

        print(
            f"{group_labels.get(g, g):<14} {n:>5} "
            f"{avg_conc*100:>6.1f}% {avg_turn_pctl*100:>7.0f}% "
            f"{avg_profit*100:>6.1f}% {avg_cost_dev:>6.1%} "
            f"{avg_ret5d:>+7.1%}±{std_ret5d:.1%} "
            f"{avg_ret10d:>+8.1%} "
            f"{avg_ret20d:>+8.1%}±{std_ret20d:.1%}"
        )

    # ── 6. 极值分析 ───────────────────────────────────────
    print()
    print("=" * 78)
    print("极值观察")
    print("=" * 78)

    most_conc = min(all_metrics, key=lambda x: x["conc"])
    least_conc = max(all_metrics, key=lambda x: x["conc"])
    highest_turn = max(all_metrics, key=lambda x: x["turn_pctl"])
    lowest_turn = min(all_metrics, key=lambda x: x["turn_pctl"])
    best_20d = max(all_metrics, key=lambda x: x["ret_20d"])
    worst_20d = min(all_metrics, key=lambda x: x["ret_20d"])

    def _tag(m: dict) -> str:
        return f"{m['code']} (G{m['group']})"

    print(f"  筹码最集中:  {_tag(most_conc)} conc={most_conc['conc']*100:.1f}%")
    print(f"  筹码最分散:  {_tag(least_conc)} conc={least_conc['conc']*100:.1f}%")
    print(f"  换手分位最高: {_tag(highest_turn)} pctl={highest_turn['turn_pctl']*100:.0f}%")
    print(f"  换手分位最低: {_tag(lowest_turn)} pctl={lowest_turn['turn_pctl']*100:.0f}%")
    print(f"  20日涨幅最大: {_tag(best_20d)} ret={best_20d['ret_20d']:+.1%}")
    print(f"  20日涨幅最小: {_tag(worst_20d)} ret={worst_20d['ret_20d']:+.1%}")

    # ── 7. 组间差异简要结论 ────────────────────────────────
    print()
    print("=" * 78)
    print("简要结论")
    print("=" * 78)

    group_avgs = {}
    for g in ["A", "B", "C", "D", "E"]:
        items = groups.get(g, [])
        if items:
            group_avgs[g] = {
                "ret20d": sum(m["ret_20d"] for m in items) / len(items),
                "ret5d": sum(m["ret_5d"] for m in items) / len(items),
                "n": len(items),
            }

    if len(group_avgs) >= 2:
        best_g = max(group_avgs, key=lambda g: group_avgs[g]["ret20d"])
        worst_g = min(group_avgs, key=lambda g: group_avgs[g]["ret20d"])
        print(
            f"  20日平均回报最佳: Group {best_g} "
            f"({group_avgs[best_g]['ret20d']:+.1%}, n={group_avgs[best_g]['n']})"
        )
        print(
            f"  20日平均回报最差: Group {worst_g} "
            f"({group_avgs[worst_g]['ret20d']:+.1%}, n={group_avgs[worst_g]['n']})"
        )
        print(
            f"  极差: {group_avgs[best_g]['ret20d'] - group_avgs[worst_g]['ret20d']:+.1%}"
        )

    # 高集中 vs 低集中在短期回报上的差异
    high_conc = [m for m in all_metrics if m["conc"] < 0.15]
    low_conc = [m for m in all_metrics if m["conc"] >= 0.25]
    if high_conc and low_conc:
        hc_avg5d = sum(m["ret_5d"] for m in high_conc) / len(high_conc)
        lc_avg5d = sum(m["ret_5d"] for m in low_conc) / len(low_conc)
        hc_avg20d = sum(m["ret_20d"] for m in high_conc) / len(high_conc)
        lc_avg20d = sum(m["ret_20d"] for m in low_conc) / len(low_conc)
        print()
        print("  高集中(conc<15%) vs 低集中(conc>25%) 回报对比:")
        print(f"    5日平均:  高集中 {hc_avg5d:>+6.1%}  vs  低集中 {lc_avg5d:>+6.1%}  (差 {hc_avg5d - lc_avg5d:+.1%})")
        print(f"    20日平均: 高集中 {hc_avg20d:>+6.1%}  vs  低集中 {lc_avg20d:>+6.1%}  (差 {hc_avg20d - lc_avg20d:+.1%})")

    print()
    print("=" * 78)
    print("分析完成")
    print("=" * 78)


if __name__ == "__main__":
    main()
