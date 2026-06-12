#!/usr/bin/env python3
"""
融合回测：东方财富 w/f + L7 prob_up 联合信号回测。

评估"两种独立信号叠加"是否能提升择时胜率。

数据源:
    - data/realtime/eastmoney_wf_history.csv (参与意愿w/关注指数f)
    - data/realtime/prob_up_log.csv (L7 prob_up)
    - systems/MindLynx-Aistock/data/stock_analysis.db (stock_daily 次日涨跌幅)

用法:
    .venv/bin/python scripts/backtest_fusion.py

建议: prob_up_log 累计30+交易日后再运行，结果更有意义。
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class WFSignal:
    date: str
    code: str
    name: str
    w: float  # 参与意愿 (desire)
    f: float  # 关注指数 (focus)
    w_grade: int  # -3 ~ +3
    f_grade: int  # -3 ~ +3


@dataclass
class ProbUpSignal:
    date: str
    code: str
    name: str
    prob_up_rf: float
    prob_up_lgb: float
    prob_up_ensemble: float
    l7_score_rf: float
    l7_score_lgb: float


@dataclass
class TradeRecord:
    date: str
    code: str
    name: str
    signal_source: str  # wf / prob_up / fusion
    signal_strength: int  # -3 ~ +3
    direction: str  # long / short / neutral
    next_day_return: float  # 次日涨跌幅(%)


# ── 加载 w/f 历史 ──────────────────────────────────

def load_wf_history() -> list[dict]:
    path = PROJECT_ROOT / "data" / "realtime" / "eastmoney_wf_history.csv"
    if not path.exists():
        print(f"⚠  未找到 w/f 历史数据: {path}")
        return []
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


# ── 加载 prob_up 历史 ──────────────────────────────

def load_prob_up_history() -> list[dict]:
    path = PROJECT_ROOT / "data" / "realtime" / "prob_up_log.csv"
    if not path.exists():
        print(f"⚠  未找到 prob_up 历史数据: {path}")
        return []
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


# ── 获取次日回报 ──────────────────────────────────

def get_next_day_return(code: str, date_str: str, cur: sqlite3.Cursor) -> float | None:
    cur.execute(
        "SELECT pct_chg FROM stock_daily WHERE code=? AND date > ? ORDER BY date LIMIT 1",
        (code, date_str),
    )
    row = cur.fetchone()
    return row[0] if row is not None else None


# ── w/f 转 L7 分数 ────────────────────────────────

def w_to_l7(w: float) -> int:
    """参与意愿转为 -3~+3 L7 分数"""
    if w >= 80:   return 3
    if w >= 65:   return 2
    if w >= 55:   return 1
    if w >= 45:   return 0
    if w >= 35:   return -1
    if w >= 20:   return -2
    return -3


def f_to_l7(f: float) -> int:
    """关注指数转为 -3~+3 L7 分数"""
    if f >= 80:   return 2
    if f >= 65:   return 1
    if f >= 55:   return 0
    if f >= 45:   return -1
    return -2


# ── 信号回测 ──────────────────────────────────────

def backtest_wf(rows: list[dict], cur: sqlite3.Cursor) -> list[TradeRecord]:
    """回测 w/f 单独信号"""
    records = []
    desire_map: dict[tuple[str, str], float] = {}
    focus_map: dict[tuple[str, str], float] = {}

    for r in rows:
        key = (r["stock_code"], r["value_date"])
        t = r["type"]
        try:
            v = float(r["value"])
        except (ValueError, TypeError):
            continue
        if t == "desire":
            desire_map[key] = v
        elif t == "focus":
            focus_map[key] = v

    for (code, date), w in desire_map.items():
        f = focus_map.get((code, date))
        if f is None:
            continue
        w_grade = w_to_l7(w)
        f_grade = f_to_l7(f)
        # 综合 w/f 信号（取均值）
        combined = (w_grade + f_grade) / 2
        if combined >= 1.5:
            direction = "long"
            strength = round(combined)
        elif combined <= -1.0:
            direction = "short"
            strength = round(abs(combined))
        else:
            direction = "neutral"
            strength = 0

        ret = get_next_day_return(code, date, cur)
        if ret is None:
            continue

        records.append(TradeRecord(
            date=date, code=code, name=r.get("stock_name", ""),
            signal_source="wf", signal_strength=strength,
            direction=direction, next_day_return=ret,
        ))
    return records


def backtest_prob_up(rows: list[dict], cur: sqlite3.Cursor) -> list[TradeRecord]:
    """回测 prob_up 单独信号"""
    records = []
    for r in rows:
        try:
            ensemble = float(r["prob_up_ensemble"])
        except (ValueError, TypeError):
            continue
        code = r["stock_code"]
        date = r["date"]

        # L7 五级阈值(与 normalizer.py 保持一致)
        if ensemble >= 59:   direction = "long";  strength = 3
        elif ensemble >= 52: direction = "long";  strength = 2
        elif ensemble >= 48: direction = "long";  strength = 1
        elif ensemble >= 42: direction = "short"; strength = 1
        elif ensemble >= 35: direction = "short"; strength = 2
        else:                direction = "short"; strength = 3

        ret = get_next_day_return(code, date, cur)
        if ret is None:
            continue

        records.append(TradeRecord(
            date=date, code=code, name=r.get("stock_name", ""),
            signal_source="prob_up", signal_strength=strength,
            direction=direction, next_day_return=ret,
        ))
    return records


def backtest_fusion(
    wf_records: list[TradeRecord],
    prob_records: list[TradeRecord],
) -> list[TradeRecord]:
    """融合回测：当 w/f 和 prob_up 方向一致时交易"""
    wf_by_key = {}
    for r in wf_records:
        wf_by_key[(r.date, r.code)] = r

    prob_by_key = {}
    for r in prob_records:
        prob_by_key[(r.date, r.code)] = r

    fusion_records = []
    common_keys = set(wf_by_key.keys()) & set(prob_by_key.keys())

    for key in sorted(common_keys):
        wf = wf_by_key[key]
        pb = prob_by_key[key]

        # 方向一致且非中性 → 增强信号
        if wf.direction == pb.direction and wf.direction != "neutral":
            strength = wf.signal_strength + pb.signal_strength
            fusion_records.append(TradeRecord(
                date=wf.date, code=wf.code, name=wf.name,
                signal_source="fusion",
                signal_strength=min(strength, 6),
                direction=wf.direction,
                next_day_return=wf.next_day_return,
            ))
    return fusion_records


# ── 统计报告 ──────────────────────────────────────

def print_stats(label: str, records: list[TradeRecord]) -> None:
    if not records:
        print(f"\n{'=' * 50}")
        print(f"📊 {label} — 无记录")
        return

    total = len(records)
    long_trades = [r for r in records if r.direction == "long"]
    short_trades = [r for r in records if r.direction == "short"]

    long_wins = [r for r in long_trades if r.next_day_return > 0]
    short_wins = [r for r in short_trades if r.next_day_return < 0]

    long_avg = sum(r.next_day_return for r in long_trades) / len(long_trades) if long_trades else 0
    short_avg = sum(r.next_day_return for r in short_trades) / len(short_trades) if short_trades else 0
    all_avg = sum(r.next_day_return for r in records) / total

    # 按信号强度分层
    strong_long = [r for r in long_trades if r.signal_strength >= 4]
    strong_short = [r for r in short_trades if r.signal_strength >= 4]

    print(f"\n{'=' * 50}")
    print(f"📊 {label}  (共 {total} 笔)")
    print(f"{'=' * 50}")
    print(f"方向分布:   做多 {len(long_trades)} / 看空 {len(short_trades)} / 中性 {total - len(long_trades) - len(short_trades)}")
    if long_trades:
        print(f"做多胜率:   {len(long_wins)}/{len(long_trades)} = {len(long_wins)/len(long_trades)*100:.1f}%  (均涨跌 {long_avg:+.2f}%)")
    if short_trades:
        print(f"看空胜率:   {len(short_wins)}/{len(short_trades)} = {len(short_wins)/len(short_trades)*100:.1f}%  (均涨跌 {short_avg:+.2f}%)")
    print(f"全场均涨跌: {all_avg:+.2f}%")
    if strong_long:
        ll = len(strong_long)
        lw = sum(1 for r in strong_long if r.next_day_return > 0)
        lavg = sum(r.next_day_return for r in strong_long) / ll
        print(f"强做多({strong_long[0].signal_strength}+): {lw}/{ll} = {lw/ll*100:.1f}%  (均涨跌 {lavg:+.2f}%)")
    if strong_short:
        sl = len(strong_short)
        sw = sum(1 for r in strong_short if r.next_day_return < 0)
        savg = sum(r.next_day_return for r in strong_short) / sl
        print(f"强看空({strong_short[0].signal_strength}+): {sw}/{sl} = {sw/sl*100:.1f}%  (均涨跌 {savg:+.2f}%)")
    print()


def main():
    print("=" * 60)
    print("  融合回测：w/f + prob_up 联合信号评估")
    print("=" * 60)

    # 连接数据库
    db_path = PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"
    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}")
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 加载数据
    wf_rows = load_wf_history()
    prob_rows = load_prob_up_history()

    if not wf_rows and not prob_rows:
        print("❌ 无可用数据")
        sys.exit(1)

    print(f"\n📅 w/f 历史:    {len(wf_rows)} 行")
    print(f"📅 prob_up 历史: {len(prob_rows)} 行")

    # 回测各信号
    wf_records = backtest_wf(wf_rows, cur)
    prob_records = backtest_prob_up(prob_rows, cur)
    fusion_records = backtest_fusion(wf_records, prob_records)

    # 打印统计
    print_stats("w/f 单独信号", wf_records)
    print_stats("prob_up 单独信号", prob_records)
    print_stats("融合信号 (方向一致时)", fusion_records)

    # 样本量提醒
    if len(prob_rows) < 300:
        print("⚠  注意: prob_up 累计不足30交易日，当前结果仅供参考。")

    conn.close()


if __name__ == "__main__":
    main()
