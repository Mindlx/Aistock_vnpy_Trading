"""Factor-only backtest baseline.

Evaluates factor composite scores against forward returns to establish
a quantitative baseline that the LLM-based analysis should beat.

Usage:
    python -m src.core.factor_backtest              # 20d window (default)
    python -m src.core.factor_backtest --window 5   # 5d window
    python -m src.core.factor_backtest --window 10  # 10d window
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import date, timedelta

import numpy as np

logger = logging.getLogger(__name__)


def _load_daily_data(db_path: str, code: str, start_date: str, end_date: str) -> list[dict]:
    """Load OHLCV data for a stock within date range."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT date, close, volume, high, low, pct_chg FROM stock_daily "
        "WHERE code=? AND date BETWEEN ? AND ? ORDER BY date",
        (code, start_date, end_date),
    ).fetchall()
    conn.close()
    return [dict(zip(["date", "close", "volume", "high", "low", "pct_chg"], r)) for r in rows]


def evaluate_factor_signals(
    db_path: str = "data/stock_analysis.db",
    eval_window: int = 20,
    step_days: int = 5,
) -> dict:
    """Run factor-only backtest across all tracked stocks.

    For each stock and each evaluation date:
    1. Compute factor composite score (using 60-day lookback)
    2. Predict: positive composite → bullish, negative → bearish
    3. Check forward return over eval_window trading days
    4. Track correct predictions

    Returns:
        dict with accuracy stats per stock and overall.
    """
    from src.core.factor_engine import FactorEngine
    from src.core.regime_factor_weights import get_regime_weights, _FALLBACK_REGIME

    conn = sqlite3.connect(db_path)
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_daily ORDER BY code").fetchall()]
    date_range = conn.execute(
        "SELECT MIN(date), MAX(date) FROM stock_daily"
    ).fetchone()
    if not date_range or not date_range[0]:
        conn.close()
        return {"error": "no data"}
    min_date, max_date = date_range
    conn.close()

    engine = FactorEngine()
    results: dict[str, dict] = {}
    total_correct = 0
    total_eval = 0

    # Pre-load all stock data to avoid per-stock DB re-reads
    all_stocks_data: dict[str, list[dict]] = {}
    for code in codes:
        data = _load_daily_data(db_path, code, str(min_date), str(max_date))
        if len(data) >= 60 + eval_window:
            all_stocks_data[code] = data

    # Prepare stock_data dict for time_series_normalize (close + volume arrays)
    stock_data: dict[str, dict[str, np.ndarray]] = {}
    for code, data in all_stocks_data.items():
        stock_data[code] = {
            "close": np.array([d["close"] for d in data], dtype=float),
            "volume": np.array([d["volume"] for d in data], dtype=float),
        }

    for code in codes:
        all_data = all_stocks_data.get(code)
        if not all_data:
            continue

        stock_correct = 0
        stock_eval = 0

        for i in range(60, len(all_data) - eval_window, step_days):
            lookback = all_data[i - 60:i]
            forward = all_data[i + eval_window - 1]
            lookback_idx_end = i  # use all data up to current point

            r = engine.compute_for_stock(code, lookback)
            # Time-series normalize this stock against its own historical distribution
            # Collect results for all stocks at this time point
            all_at_time = [r]
            for other_code, other_data in all_stocks_data.items():
                if other_code == code:
                    continue
                if len(other_data) > lookback_idx_end:
                    o_lookback = other_data[lookback_idx_end - 60:lookback_idx_end]
                    o_r = engine.compute_for_stock(other_code, o_lookback)
                    all_at_time.append(o_r)

            if len(all_at_time) >= 2:
                engine.time_series_normalize(all_at_time, stock_data, lookback=120)

            # Apply regime-conditional factor weights (use fallback regime)
            regime_weights = get_regime_weights(_FALLBACK_REGIME)
            if regime_weights:
                engine.apply_regime_weights(regime_weights)

            start_price = float(lookback[-1]["close"])
            end_price = float(forward["close"])
            fwd_return = (end_price - start_price) / start_price if start_price > 0 else 0.0

            predicted_bullish = r.composite_score > 0.0
            actual_bullish = fwd_return > 0.0

            stock_eval += 1
            if predicted_bullish == actual_bullish:
                stock_correct += 1

        if stock_eval > 0:
            acc = stock_correct / stock_eval * 100
            results[code] = {
                "evaluations": stock_eval,
                "correct": stock_correct,
                "accuracy_pct": round(acc, 1),
            }
            total_correct += stock_correct
            total_eval += stock_eval

    overall = {
        "total_evaluations": total_eval,
        "total_correct": total_correct,
        "overall_accuracy_pct": round(total_correct / total_eval * 100, 1) if total_eval > 0 else 0.0,
        "stocks_evaluated": len(results),
        "per_stock": results,
    }

    # Persist IC/IR monitor data and generate report
    try:
        from src.core.factor_monitor import FactorMonitor

        monitor = FactorMonitor()
        if monitor.enabled and total_eval > 0:
            monitor.save_data()
            monitor.generate_report()
    except Exception:
        pass

    return overall


def print_factor_backtest_report(results: dict, eval_window: int = 20) -> None:
    """Print a human-readable factor backtest report."""
    print("=" * 60)
    print("  因子-only 回测基准 (Factor Backtest Baseline)")
    print(f"  评估窗口: {eval_window} 交易日  步长: 5 天")
    print(f"  归一化: time_series (每只股票自身上百天历史分布)")
    print("=" * 60)

    per_stock = results.get("per_stock", {})
    if per_stock:
        print(f"\n{'代码':<10} {'评估次数':>8} {'正确':>6} {'准确率':>8}")
        print("-" * 35)
        for code, stats in sorted(per_stock.items(), key=lambda x: x[1]["accuracy_pct"], reverse=True):
            print(f"{code:<10} {stats['evaluations']:>8} {stats['correct']:>6} {stats['accuracy_pct']:>7.1f}%")

    print(f"\n整体准确率: {results.get('overall_accuracy_pct', 0):.1f}%")
    print(f"总评估次数: {results.get('total_evaluations', 0)}")
    print(f"评估股票数: {results.get('stocks_evaluated', 0)}")

    accuracy = results.get("overall_accuracy_pct", 0)
    if accuracy > 55:
        print("结论: ✅ 因子信号具有显著预测能力 (>55%)")
    elif accuracy > 50:
        print("结论: ⚠️ 因子信号略优于随机 (50-55%)")
    else:
        print("结论: ❌ 因子信号无预测能力 (≤50%)")

    print(f"\n> 对比 LLM：若 LLM 综合评分准确率 > {accuracy:.1f}%, 则 AI 增值。")
    print("> 反之则说明 LLM 在添加噪音，应降低 LLM 权重或切换到因子-only 模式。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Factor-only backtest")
    parser.add_argument("--window", type=int, default=20, choices=[5, 10, 20],
                        help="Evaluation window in trading days (default: 20)")
    parser.add_argument("--all-windows", action="store_true",
                        help="Run all windows (5d, 10d, 20d)")
    args = parser.parse_args()

    windows = [5, 10, 20] if args.all_windows else [args.window]
    for w in windows:
        results = evaluate_factor_signals(eval_window=w)
        if "error" in results:
            print(f"Error: {results['error']}")
        else:
            print_factor_backtest_report(results, eval_window=w)
            print()
