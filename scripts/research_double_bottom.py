#!/usr/bin/env python3
"""
W双底形态回测研究脚本
====================
独立脚本，验证 W双底高中间峰变体 vs 标准双底 的预测表现差异。

数据来源: SQLite data_warehouse.db -> daily_ohlcv 表
检测算法: 滑动窗口扫描双底形态，分类为"标准"和"高中间峰"两种变体
统计输出: 触发次数、胜率、avg_ret、t-test 差异显著性

用法: python scripts/research_double_bottom.py
"""

import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
from scipy import stats

# ── 配置 ────────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "data_warehouse.db"
WINDOW = 120           # 滑动窗口大小（交易日）
STEP = 5               # 窗口步长
LOW_DIFF_THRESHOLD = 0.03    # 双底低点差阈值 (< 3%)
MID_PEAK_THRESHOLD = 0.03    # 中间峰高度阈值 (> lo1 * 1.03)
ELEVATED_THRESHOLD = 0.01    # 高中间峰判定阈值 (> left_shoulder * 1.01)
MIN_LO_SPAN = 5              # 两底最少间隔交易日
FORWARD_DAYS = [5, 10, 20]   # 前向收益观察期


@dataclass
class Signal:
    """一次双底信号"""
    stock_code: str
    date: str              # lo2 的日期
    lo1_idx: int
    lo2_idx: int
    lo1_price: float
    lo2_price: float
    mid_high: float
    left_shoulder: float
    variant: str           # "标准" or "高中间峰"
    forward_returns: dict  # {5: ret, 10: ret, 20: ret}


@dataclass
class StatsAccumulator:
    """统计累加器"""
    signals: List[Signal] = field(default_factory=list)

    def add(self, sig: Signal):
        self.signals.append(sig)

    def by_variant(self, variant: str) -> List[Signal]:
        return [s for s in self.signals if s.variant == variant]


def load_ohlcv(db_path: Path) -> dict:
    """从 SQLite 加载所有股票的日线数据，按 stock_code -> sorted list of dicts"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT stock_code FROM daily_ohlcv GROUP BY stock_code ORDER BY stock_code"
    )
    codes = [r["stock_code"] for r in cur.fetchall()]

    data = {}
    for code in codes:
        cur.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM daily_ohlcv WHERE stock_code = ? ORDER BY date",
            (code,),
        )
        rows = cur.fetchall()
        data[code] = [dict(r) for r in rows]

    conn.close()
    return data


def detect_double_bottom(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    dates: List[str],
) -> Optional[dict]:
    """
    在单个窗口内检测双底形态。
    返回 dict 包含检测到的信号信息，未检测到则返回 None。
    """
    n = len(lows)
    if n < 20:
        return None

    # 取最低的 5 个 low 的索引，按位置排序
    low_indices = sorted(range(n), key=lambda i: lows[i])[:5]
    if len(low_indices) < 2:
        return None

    lo1_idx, lo2_idx = low_indices[0], low_indices[1]

    # 间距 >= 5 天
    if lo2_idx - lo1_idx < MIN_LO_SPAN:
        return None

    # 两底价格差 < 3%
    lo1_price = lows[lo1_idx]
    lo2_price = lows[lo2_idx]
    price_diff = abs(lo1_price - lo2_price) / max(lo1_price, lo2_price)
    if price_diff >= LOW_DIFF_THRESHOLD:
        return None

    # 中间高点 > lo1 * 1.03
    mid_high = max(highs[lo1_idx : lo2_idx + 1])
    if mid_high <= lo1_price * (1 + MID_PEAK_THRESHOLD):
        return None

    # 左侧肩部（窗口起点到 lo1 的最高 high）
    if lo1_idx >= 2:
        left_shoulder = max(highs[: lo1_idx + 1])
    else:
        left_shoulder = highs[0]

    # 分类
    is_elevated = mid_high > left_shoulder * (1 + ELEVATED_THRESHOLD)
    variant = "高中间峰" if is_elevated else "标准"

    return {
        "lo1_idx": lo1_idx,
        "lo2_idx": lo2_idx,
        "lo1_price": lo1_price,
        "lo2_price": lo2_price,
        "mid_high": mid_high,
        "left_shoulder": left_shoulder,
        "variant": variant,
        "date": dates[lo2_idx],
        "entry_price": lo2_price,
    }


def compute_forward_returns(
    closes: List[float],
    lo2_idx: int,
    forward_days: List[int],
) -> dict:
    """计算从 lo2 位置开始的多个前向期收益"""
    returns = {}
    for fd in forward_days:
        target_idx = lo2_idx + fd
        if target_idx < len(closes):
            ret = (closes[target_idx] - closes[lo2_idx]) / closes[lo2_idx]
            returns[fd] = ret
        else:
            returns[fd] = float("nan")
    return returns


def scan_stock_signals(
    code: str,
    ohlcv: List[dict],
    window: int = WINDOW,
    step: int = STEP,
) -> List[Signal]:
    """对单只股票进行滑动窗口扫描"""
    n = len(ohlcv)
    if n < window + max(FORWARD_DAYS):
        return []

    dates = [r["date"] for r in ohlcv]
    highs = [r["high"] for r in ohlcv]
    lows = [r["low"] for r in ohlcv]
    closes = [r["close"] for r in ohlcv]

    signals: List[Signal] = []
    seen_dates = set()  # 去重：同一日期不重复记录

    for start in range(0, n - window + 1, step):
        end = start + window

        result = detect_double_bottom(
            highs[start:end],
            lows[start:end],
            closes[start:end],
            dates[start:end],
        )
        if result is None:
            continue

        # lo2 在原始序列中的绝对索引
        abs_lo2 = start + result["lo2_idx"]

        if dates[abs_lo2] in seen_dates:
            continue
        seen_dates.add(dates[abs_lo2])

        fwd = compute_forward_returns(closes, abs_lo2, FORWARD_DAYS)

        signals.append(
            Signal(
                stock_code=code,
                date=dates[abs_lo2],
                lo1_idx=start + result["lo1_idx"],
                lo2_idx=abs_lo2,
                lo1_price=result["lo1_price"],
                lo2_price=result["lo2_price"],
                mid_high=result["mid_high"],
                left_shoulder=result["left_shoulder"],
                variant=result["variant"],
                forward_returns=fwd,
            )
        )

    return signals


def print_results(accumulator: StatsAccumulator):
    """输出统计结果"""
    total = len(accumulator.signals)
    if total == 0:
        print("\n  未检测到任何双底信号，检查参数或数据。")
        return

    print(f"\n{'=' * 78}")
    print("  W双底形态回测报告")
    print(f"{'=' * 78}")
    print(f"  数据源: {DB_PATH}")
    print(f"  窗口: {WINDOW}天, 步长: {STEP}天")
    all_dates = [s.date for s in accumulator.signals]
    print(f"  扫描日期范围: {min(all_dates)} → {max(all_dates)}")
    print(f"  总信号数: {total}")
    print()

    # ── 按变体分组统计 ──
    variant_names = ["标准", "高中间峰"]
    print(f"{'─' * 78}")
    print("  变体统计对比")
    print(f"{'─' * 78}")

    header = (
        f'{"变体":<12} {"次数":>6} '
        f'{"胜率_5d":>8} {"avg_ret_5d":>10} {"median_5d":>10} '
        f'{"胜率_10d":>8} {"avg_ret_10d":>10} {"median_10d":>10} '
        f'{"胜率_20d":>8} {"avg_ret_20d":>10} {"median_20d":>10}'
    )
    print(header)
    print(f"{'─' * 78}")

    for v in variant_names:
        sigs = accumulator.by_variant(v)
        count = len(sigs)

        if count == 0:
            print(f'{v:<12} {count:>6} {"N/A":>8} {"N/A":>10} {"N/A":>10} '
                  f'{"N/A":>8} {"N/A":>10} {"N/A":>10} '
                  f'{"N/A":>8} {"N/A":>10} {"N/A":>10}')
            continue

        line_parts = [f"{v:<12}", f"{count:>6}"]
        for fd in FORWARD_DAYS:
            rets = [s.forward_returns[fd] for s in sigs if not np.isnan(s.forward_returns[fd])]
            if not rets:
                line_parts.extend(["N/A".rjust(8), "N/A".rjust(10), "N/A".rjust(10)])
                continue
            avg_r = statistics.mean(rets)
            med_r = statistics.median(rets)
            win_rate = sum(1 for r in rets if r > 0) / len(rets)
            line_parts.append(f"{win_rate:>7.1%}")
            line_parts.append(f"{avg_r:>10.2%}")
            line_parts.append(f"{med_r:>10.2%}")
        print("".join(line_parts))

    # ── 前向收益分布详情 ──
    print(f"\n{'─' * 78}")
    print("  前向收益详细分布")
    print(f"{'─' * 78}")

    for v in variant_names:
        sigs = accumulator.by_variant(v)
        count = len(sigs)
        print(f"\n  [{v}] 共 {count} 次触发")

        for fd in FORWARD_DAYS:
            rets = sorted([s.forward_returns[fd] for s in sigs if not np.isnan(s.forward_returns[fd])])
            if not rets:
                continue
            avg_r = statistics.mean(rets)
            med_r = statistics.median(rets)
            std_r = statistics.stdev(rets) if len(rets) > 1 else 0.0
            win_rate = sum(1 for r in rets if r > 0) / len(rets)
            p10 = rets[int(len(rets) * 0.1)]
            p25 = rets[int(len(rets) * 0.25)]
            p75 = rets[int(len(rets) * 0.75)]
            p90 = rets[int(len(rets) * 0.9)]

            print(f"    {fd}日: avg={avg_r:+.2%}  median={med_r:+.2%}  "
                  f"std={std_r:.2%}  胜率={win_rate:.1%}  "
                  f"[P10={p10:+.2%}, P25={p25:+.2%}, P75={p75:+.2%}, P90={p90:+.2%}]")

    # ── 变体间统计差异 (t-test) ──
    print(f"\n{'─' * 78}")
    print("  变体间统计差异 (Welch's t-test, 双尾)")
    print(f"{'─' * 78}")

    sigs_standard = accumulator.by_variant("标准")
    sigs_elevated = accumulator.by_variant("高中间峰")

    header = f'{"对比项":<28} {"t-stat":>10} {"p-value":>10} {"显著(p<0.05)":>10}'
    print(header)
    print(f"{'─' * 78}")

    for fd in FORWARD_DAYS:
        r1 = [s.forward_returns[fd] for s in sigs_standard if not np.isnan(s.forward_returns[fd])]
        r2 = [s.forward_returns[fd] for s in sigs_elevated if not np.isnan(s.forward_returns[fd])]

        label = f"{fd}日 forward return"

        if len(r1) < 2 or len(r2) < 2:
            print(f'{label:<28} {"N/A":>10} {"N/A":>10} {"-":>10}')
            continue

        t_stat, p_val = stats.ttest_ind(r1, r2, equal_var=False)
        significant = "是" if p_val < 0.05 else "否"
        print(f'{label:<28} {t_stat:>10.4f} {p_val:>10.4f} {significant:>10}')

    # ── 信号按股票分布 ──
    print(f"\n{'─' * 78}")
    print("  按股票分布")
    print(f"{'─' * 78}")

    code_counts = {}
    code_variants = {}
    for s in accumulator.signals:
        code_counts[s.stock_code] = code_counts.get(s.stock_code, 0) + 1
        if s.stock_code not in code_variants:
            code_variants[s.stock_code] = {"标准": 0, "高中间峰": 0}
        code_variants[s.stock_code][s.variant] += 1

    print(f'  {"股票代码":<12} {"总次数":>8} {"标准":>8} {"高中间峰":>8}')
    print(f'  {"─" * 12} {"─" * 8} {"─" * 8} {"─" * 8}')
    for code in sorted(code_counts):
        print(f'  {code:<12} {code_counts[code]:>8} '
              f'{code_variants[code]["标准"]:>8} {code_variants[code]["高中间峰"]:>8}')

    print(f"\n{'=' * 78}\n")


def main():
    print("W双底形态回测研究")
    print(f"数据源: {DB_PATH}")

    if not DB_PATH.exists():
        print(f"错误: 数据库不存在: {DB_PATH}")
        return

    # 加载数据
    ohlcv_data = load_ohlcv(DB_PATH)
    codes = sorted(ohlcv_data.keys())
    print(f"股票数量: {len(codes)}")
    for code in codes:
        n = len(ohlcv_data[code])
        dates = [r["date"] for r in ohlcv_data[code]]
        print(f"  {code}: {n} 行, {dates[0]} → {dates[-1]}")
    print(f"扫描参数: window={WINDOW}d, step={STEP}d")
    print()

    # 扫描
    accumulator = StatsAccumulator()
    for code in codes:
        sigs = scan_stock_signals(code, ohlcv_data[code])
        for sig in sigs:
            accumulator.add(sig)
        std_count = sum(1 for s in sigs if s.variant == "标准")
        elev_count = sum(1 for s in sigs if s.variant == "高中间峰")
        if sigs:
            print(f"  {code}: {len(sigs)} 个信号 ({std_count} 标准, {elev_count} 高中间峰)")
        else:
            print(f"  {code}: 0 个信号")

    # 输出结果
    print_results(accumulator)


if __name__ == "__main__":
    main()
