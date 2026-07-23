#!/usr/bin/env python3
"""
换手率 × 筹码交叉信号回测验证脚本

目标: 对比"单因子 turnover_sentiment" vs "交叉信号（换手率+筹码代理）"的预测准确率

用法:
    python scripts/verify_turnover_chip.py                          # 默认20日窗口
    python scripts/verify_turnover_chip.py --window 5               # 5日窗口
    python scripts/verify_turnover_chip.py --window 10              # 10日窗口

输出:
    回测结束后打印对比报告，显示 baseline vs cross-signal 的准确率差异

数据流:
    1. 从 SQLite 加载每日数据 (close, volume, high, low, pct_chg)
    2. 计算 baseline: turnover_sentiment (现有因子)
    3. 计算 cross-signal: 结合换手率 + 筹码代理（价格位置+量能集中趋势）
    4. 对每个评估节点，比较两者预测方向与未来收益方向
    5. 汇总准确率对比

缺省假设:
    - 暂无历史筹码分布数据，使用 OHLCV 衍生特征作筹码代理：
      * 价格在 60 日区间中的位置 → chg_price_pos
      * 量能集中趋势（volume 的滚动集中度）→ chg_vol_conc
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import date, timedelta

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 数据加载 ──────────────────────────────────────────────


def _detect_db_schema(conn: sqlite3.Connection) -> str:
    """Auto-detect DB type: 'warehouse' (data_warehouse.db) or 'legacy' (stock_analysis.db)."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "daily_ohlcv" in tables:
        return "warehouse"
    return "legacy"


def load_daily_data(
    db_path: str, code: str, start_date: str, end_date: str
) -> list[dict]:
    """Load OHLCV + turnover data for a stock within date range.

    Supports both data_warehouse.db (daily_ohlcv table, has turnover) 
    and stock_analysis.db (stock_daily table, no turnover).
    """
    conn = sqlite3.connect(db_path)
    schema = _detect_db_schema(conn)
    if schema == "warehouse":
        rows = conn.execute(
            "SELECT date, close, volume, high, low, pct_chg, turnover FROM daily_ohlcv "
            "WHERE stock_code=? AND date BETWEEN ? AND ? ORDER BY date",
            (code, start_date, end_date),
        ).fetchall()
        keys = ["date", "close", "volume", "high", "low", "pct_chg", "turnover"]
    else:
        rows = conn.execute(
            "SELECT date, close, volume, high, low, pct_chg FROM stock_daily "
            "WHERE code=? AND date BETWEEN ? AND ? ORDER BY date",
            (code, start_date, end_date),
        ).fetchall()
        keys = ["date", "close", "volume", "high", "low", "pct_chg"]
    conn.close()
    return [dict(zip(keys, r)) for r in rows]


def get_stock_codes(db_path: str) -> list[str]:
    """Get distinct stock codes from DB."""
    conn = sqlite3.connect(db_path)
    schema = _detect_db_schema(conn)
    if schema == "warehouse":
        codes = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT stock_code FROM daily_ohlcv ORDER BY stock_code"
            ).fetchall()
        ]
    else:
        codes = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT code FROM stock_daily ORDER BY code"
            ).fetchall()
        ]
    conn.close()
    return codes


def get_date_range(db_path: str) -> tuple[str | None, str | None]:
    """Get min/max date from DB."""
    conn = sqlite3.connect(db_path)
    schema = _detect_db_schema(conn)
    if schema == "warehouse":
        row = conn.execute("SELECT MIN(date), MAX(date) FROM daily_ohlcv").fetchone()
    else:
        row = conn.execute("SELECT MIN(date), MAX(date) FROM stock_daily").fetchone()
    conn.close()
    return row if row else (None, None)


# ── Baseline: 现有 turnover_sentiment ─────────────────────


def compute_turnover_sentiment(volume: np.ndarray, window: int = 20) -> float:
    """Existing turnover_sentiment: high ratio = bearish.
    Returns turnover_ratio (same as existing factor_engine implementation).
    """
    if len(volume) < window + 1:
        return 1.0
    recent = volume[-1]
    avg_vol = float(np.mean(volume[-(window + 1) : -1]))
    return recent / avg_vol if avg_vol > 0 else 1.0


# ── Cross-signal: 换手率 × 筹码代理 ───────────────────────


def compute_price_position(close: np.ndarray, high: np.ndarray, low: np.ndarray) -> float:
    """Price position in 60-day range: 0.0 (bottom) ~ 1.0 (top).
    Proxy for chip cost distribution — when price is at the top of range,
    more holders are in profit.
    """
    n = len(close)
    lookback = min(n, 60)
    recent_high = float(np.max(high[-lookback:]))
    recent_low = float(np.min(low[-lookback:]))
    current = float(close[-1])
    if recent_high - recent_low < 1e-8:
        return 0.5
    return (current - recent_low) / (recent_high - recent_low)


def compute_vol_concentration(volume: np.ndarray, window: int = 20) -> float:
    """Volume concentration: CV of recent volume.
    Low CV = stable/quiet = concentrated (holders not trading).
    High CV = erratic = dispersing (active distribution).
    Proxy for chip concentration when actual chip data unavailable.
    """
    if len(volume) < window:
        return 1.0
    recent = volume[-window:]
    mean = float(np.mean(recent))
    std = float(np.std(recent))
    return std / mean if mean > 0 else 1.0


def compute_vol_trend(volume: np.ndarray, short_w: int = 5, long_w: int = 20) -> float:
    """Volume trend: short vs long average ratio.
    Proxy for turnover trend (放量/缩量).
    """
    if len(volume) < long_w:
        return 1.0
    short_avg = float(np.mean(volume[-short_w:]))
    long_avg = float(np.mean(volume[-long_w:]))
    return short_avg / long_avg if long_avg > 0 else 1.0


def compute_turnover_percentile(
    turn_rate: np.ndarray | None, volume: np.ndarray, window: int = 60
) -> float:
    """Turnover percentile: latest value vs rolling window.
    优先使用真实换手率数据，降级到 volume proxy。
    0.0 = 最低, 1.0 = 最高.
    """
    series = turn_rate if turn_rate is not None else volume
    if series is None or len(series) < window + 1:
        return 0.5
    current = series[-1]
    hist = series[-(window + 1) : -1]
    if len(hist) == 0:
        return 0.5
    count_less = float(np.sum(hist < current))
    return count_less / len(hist)


def compute_concentration(
    turn_rate: np.ndarray | None, volume: np.ndarray, window: int = 20
) -> float:
    """筹码集中度代理: 换手率 CV 越低 = 筹码越集中。
    
    使用真实换手率的 CV（Coefficient of Variation）代替 volume CV。
    因为换手率直接衡量筹码交换频率，比 volume 更准确地反映筹码锁定程度。
    """
    series = turn_rate if turn_rate is not None else volume
    if series is None or len(series) < window:
        return 1.0
    recent = series[-window:]
    mean = float(np.mean(recent))
    std = float(np.std(recent))
    return std / mean if mean > 0 else 1.0


def compute_vol_trend(
    turn_rate: np.ndarray | None, volume: np.ndarray,
    short_w: int = 5, long_w: int = 20,
) -> float:
    """换手率趋势: 短期均值 / 长期均值。优先使用真实换手率。
     > 1.0 = 放量, < 1.0 = 缩量.
    """
    series = turn_rate if turn_rate is not None else volume
    if series is None or len(series) < long_w:
        return 1.0
    short_avg = float(np.mean(series[-short_w:]))
    long_avg = float(np.mean(series[-long_w:]))
    return short_avg / long_avg if long_avg > 0 else 1.0


def compute_cross_signal(
    close: np.ndarray,
    volume: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    turn_rate: np.ndarray | None = None,
) -> tuple[float, list[str]]:
    """Compute cross-signal score.

    Positive = bullish, Negative = bearish.
    优先使用真实换手率数据，降级到 volume proxy.
    阈值使用滚动分位自适应（per-stock normalization），
    可跨不同换手率水平的品种使用。

    Returns (score, triggered_scenarios).
    """
    n = len(close)
    if n < 60:
        return 0.0, []

    price_pos = compute_price_position(close, high, low)
    turn_pct = compute_turnover_percentile(turn_rate, volume)
    chip_cv = compute_concentration(turn_rate, volume)
    trend = compute_vol_trend(turn_rate, volume)

    # ── 阈值（基于分位自适应）──
    is_low = price_pos < 0.25
    is_high = price_pos > 0.75
    is_low_turn = turn_pct < 0.30
    is_high_turn = turn_pct > 0.70
    is_extreme_turn = turn_pct > 0.92
    conc_high = chip_cv < 0.7
    conc_low = chip_cv > 1.0

    score = 0.0
    triggered = []

    # 低位场景
    if is_low:
        # A1: 低位 + 地量 + 筹码集中 = 底部蓄力（看多）
        if is_low_turn and conc_high:
            score += 0.6
            triggered.append("A1")
        # A3: 低位 + 放量 + 筹码集中 = 吸筹末期（看多）
        elif is_high_turn and conc_high:
            score += 0.7
            triggered.append("A3")
        # A4: 低位 + 放量 + 筹码发散 = 散户互砍（看空）
        elif is_high_turn and conc_low:
            score -= 0.5
            triggered.append("A4")
        # A5: 低位 + 温和放量（不极端）+ 集中 = 观察区（偏多）
        elif not is_low_turn and not is_extreme_turn and conc_high:
            score += 0.3
            triggered.append("A5")

    # 高位场景
    if is_high:
        # B1/B5: 高位 + 放量 + 筹码集中 = 空中加油（看多）
        if is_high_turn and conc_high:
            score += 0.5
            triggered.append("B1/B5")
        # B2/B6: 高位 + 放量 + 筹码发散 = 主力出货（看空）
        elif is_high_turn and conc_low:
            score -= 0.7
            triggered.append("B2/B6")
        # B3: 高位 + 缩量 + 筹码集中 = 锁仓拉升（看多）
        elif is_low_turn and conc_high:
            score += 0.4
            triggered.append("B3")
        # B7: 高位 + 正常换手 + 筹码发散 = 顶部形成中（偏空）
        elif not is_low_turn and not is_high_turn and conc_low:
            score -= 0.3
            triggered.append("B7")

    return float(np.clip(score, -1.0, 1.0)), triggered


# ── 评估 ──────────────────────────────────────────────────


def evaluate_signals(
    db_path: str = "data/stock_analysis.db",
    eval_window: int = 20,
    step_days: int = 5,
) -> dict:
    """Run cross-signal vs baseline comparison backtest.

    For each stock and evaluation date:
    1. Compute baseline = turnover_sentiment (existing factor)
    2. Compute cross_signal = turnover × chip proxy interaction
    3. Compare both against forward return
    """
    codes = get_stock_codes(db_path)
    min_date, max_date = get_date_range(db_path)
    if not min_date or not max_date:
        return {"error": "no data"}

    all_data: dict[str, list[dict]] = {}
    for code in codes:
        data = load_daily_data(db_path, code, str(min_date), str(max_date))
        if len(data) >= 60 + eval_window:
            all_data[code] = data

    # Stats
    baseline_correct = 0
    baseline_total = 0
    cross_correct = 0
    cross_total = 0
    both_correct = 0
    cross_better = 0
    baseline_better = 0

    per_stock: dict[str, dict] = {}
    scenario_hits: dict[str, int] = {}

    for code, data in all_data.items():
        stock_baseline_correct = 0
        stock_baseline_total = 0
        stock_cross_correct = 0
        stock_cross_total = 0

        for i in range(60, len(data) - eval_window, step_days):
            lookback = data[i - 60: i]
            forward = data[i + eval_window - 1]

            close = np.array([d["close"] for d in lookback], dtype=float)
            volume = np.array([d["volume"] for d in lookback], dtype=float)
            high = np.array([d["high"] for d in lookback], dtype=float)
            low = np.array([d["low"] for d in lookback], dtype=float)
            turn_rate = (
                np.array([d.get("turnover", 0) for d in lookback], dtype=float)
                if "turnover" in lookback[0]
                else None
            )

            # Baseline: turnover_sentiment (high ratio = bearish)
            turn_ratio = compute_turnover_sentiment(volume)
            baseline_bearish = turn_ratio > 1.5

            # Cross-signal (使用真实换手率 if available)
            cross_score, triggered = compute_cross_signal(close, volume, high, low, turn_rate)
            cross_bullish = cross_score > 0.0
            cross_neutral = abs(cross_score) < 0.1

            # Forward return
            start_price = float(lookback[-1]["close"])
            end_price = float(forward["close"])
            fwd_return = (end_price - start_price) / start_price if start_price > 0 else 0.0
            actual_bullish = fwd_return > 0.0

            # Track scenario hit counts
            for sig in triggered:
                scenario_hits[sig] = scenario_hits.get(sig, 0) + 1

            # Evaluate baseline (only when it has a signal)
            if turn_ratio > 1.3 or turn_ratio < 0.7:
                stock_baseline_total += 1
                predicted_bearish_baseline = baseline_bearish
                baseline_pred_bullish = not predicted_bearish_baseline
                if baseline_pred_bullish == actual_bullish:
                    stock_baseline_correct += 1

            # Evaluate cross-signal (only when non-neutral)
            if not cross_neutral:
                stock_cross_total += 1
                if cross_bullish == actual_bullish:
                    stock_cross_correct += 1

                # Head-to-head comparison
                if stock_baseline_total > 0 and stock_baseline_total == stock_cross_total:
                    last_baseline_correct = (
                        baseline_pred_bullish == actual_bullish
                    )
                    if cross_bullish == actual_bullish and not last_baseline_correct:
                        cross_better += 1
                    if not (cross_bullish == actual_bullish) and last_baseline_correct:
                        baseline_better += 1

        if stock_baseline_total > 0 or stock_cross_total > 0:
            per_stock[code] = {
                "baseline": {
                    "eval": stock_baseline_total,
                    "correct": stock_baseline_correct,
                    "acc": round(stock_baseline_correct / max(stock_baseline_total, 1) * 100, 1),
                },
                "cross": {
                    "eval": stock_cross_total,
                    "correct": stock_cross_correct,
                    "acc": round(stock_cross_correct / max(stock_cross_total, 1) * 100, 1),
                },
            }

        baseline_correct += stock_baseline_correct
        baseline_total += stock_baseline_total
        cross_correct += stock_cross_correct
        cross_total += stock_cross_total

    return {
        "baseline_accuracy": round(baseline_correct / max(baseline_total, 1) * 100, 1),
        "cross_accuracy": round(cross_correct / max(cross_total, 1) * 100, 1),
        "baseline_eval": baseline_total,
        "cross_eval": cross_total,
        "baseline_correct": baseline_correct,
        "cross_correct": cross_correct,
        "cross_better_count": cross_better,
        "baseline_better_count": baseline_better,
        "stocks_evaluated": len(per_stock),
        "per_stock": per_stock,
        "scenario_hits": dict(sorted(scenario_hits.items(), key=lambda x: -x[1])),
    }


# ── 报告 ──────────────────────────────────────────────────


def print_report(results: dict, eval_window: int) -> None:
    """Print comparison report."""
    print()
    print("=" * 70)
    print("  换手率 × 筹码交叉信号 — 回测验证报告")
    print(f"  评估窗口: {eval_window} 交易日  步长: 5 天")
    print("=" * 70)

    ba = results.get("baseline_accuracy", 0)
    ca = results.get("cross_accuracy", 0)
    be = results.get("baseline_eval", 0)
    ce = results.get("cross_eval", 0)
    bc = results.get("baseline_correct", 0)
    cc = results.get("cross_correct", 0)

    print(f"\n  Baseline (turnover_sentiment 单因子):")
    print(f"    准确率: {ba:.1f}%  ({bc}/{be})")
    print(f"\n  Cross-signal (换手率 × 筹码代理):")
    print(f"    准确率: {ca:.1f}%  ({cc}/{ce})")
    print(f"\n  差异: {ca - ba:+.1f}%  ", end="")
    if ca > ba:
        print("✅ 交叉信号优于 baseline")
    elif ca < ba:
        print("❌ 交叉信号劣于 baseline")
    else:
        print("＝ 两者持平")

    print(f"\n  正面交锋:")
    print(f"    交叉信号更准: {results.get('cross_better_count', 0)} 次")
    print(f"    baseline 更准: {results.get('baseline_better_count', 0)} 次")

    print(f"\n  场景触发统计 (按频次排序):")
    for sig, count in results.get("scenario_hits", {}).items():
        print(f"    {sig}: {count} 次")

    print(f"\n  评估股票数: {results.get('stocks_evaluated', 0)}")
    print()

    # ── 结论 ──
    print("─" * 70)
    if ca > ba + 3:
        print("结论: ✅ 交叉信号带来显著提升 (>3pp)，建议集成到 factor_engine")
    elif ca > ba:
        print("结论: ⚠️ 交叉信号小幅提升 (0-3pp)，需更多数据验证")
    elif ca < ba - 3:
        print("结论: ❌ 交叉信号显著更差，芯片代理可能引入噪音")
    else:
        print("结论: ＝ 两者接近，交叉信号无显著改善")
    print()


# ── 入口 ──────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Turnover × Chip cross-signal backtest verification"
    )
    parser.add_argument(
        "--window",
        type=int,
        default=20,
        choices=[5, 10, 20],
        help="Evaluation window in trading days (default: 20)",
    )
    parser.add_argument(
        "--all-windows",
        action="store_true",
        help="Run all windows (5d, 10d, 20d)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="data/data_warehouse.db",
        help="Path to stock daily SQLite database (auto-detects schema: warehouse vs legacy)",
    )
    args = parser.parse_args()

    windows = [5, 10, 20] if args.all_windows else [args.window]
    for w in windows:
        print(f"\n运行评估: window={w}d")
        results = evaluate_signals(db_path=args.db, eval_window=w)
        if "error" in results:
            print(f"Error: {results['error']}")
        else:
            print_report(results, eval_window=w)


if __name__ == "__main__":
    main()
