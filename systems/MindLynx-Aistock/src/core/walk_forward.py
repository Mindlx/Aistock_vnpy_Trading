"""Walk-Forward Analysis for backtest validation.

Validates that parameter optimization results are not overfit by
splitting data into sequential train/validation windows.

Based on the Walk-Forward Analysis framework from the quant research report
and Qlib/Zipline methodology.

Usage:
    from src.core.walk_forward import walk_forward_analysis
    results = walk_forward_analysis(daily_returns, train=60, test=20, step=10)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from src.core.perf_analyzers import analyze_performance, compute_sharpe_ratio

logger = logging.getLogger(__name__)


@dataclass
class WFAWindow:
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    in_sample_sharpe: float = 0.0
    out_of_sample_sharpe: float = 0.0
    oos_positive: bool = False


@dataclass
class WFAResult:
    windows: list[WFAWindow]
    mean_is_sharpe: float = 0.0
    mean_oos_sharpe: float = 0.0
    oos_positive_ratio: float = 0.0
    decay_ratio: float = 0.0
    is_robust: bool = False
    verdict: str = ""

    def to_dict(self) -> dict:
        return {
            "windows": len(self.windows),
            "mean_is_sharpe": round(self.mean_is_sharpe, 3),
            "mean_oos_sharpe": round(self.mean_oos_sharpe, 3),
            "oos_positive_ratio": round(self.oos_positive_ratio, 2),
            "decay_ratio": round(self.decay_ratio, 3),
            "is_robust": self.is_robust,
            "verdict": self.verdict,
        }


def walk_forward_analysis(
    daily_returns: list[float],
    train_window: int = 60,
    test_window: int = 20,
    step: int = 10,
) -> WFAResult:
    """Perform walk-forward validation on a return series.

    Splits the data into overlapping windows:
        Train: [i, i+train_window)
        Test:  [i+train_window, i+train_window+test_window)
        Step:  advance i by `step` trading days

    For each window, computes in-sample and out-of-sample Sharpe.
    Compares IS vs OOS to detect overfitting (decay_ratio).

    A strategy is robust if:
        - decay_ratio < 0.4 (OOS Sharpe doesn't degrade too much from IS)
        - mean OOS Sharpe > 1.0
        - >50% of windows have positive OOS Sharpe

    Args:
        daily_returns: time-ordered daily return series
        train_window: training period in trading days
        test_window: testing/validation period in trading days
        step: advance step between windows

    Returns:
        WFAResult with per-window metrics and overall verdict.
    """
    n = len(daily_returns)
    if n < train_window + test_window:
        return WFAResult(
            windows=[],
            verdict=f"数据不足 (需要至少 {train_window + test_window} 天，当前 {n} 天)",
        )

    windows = []
    for start in range(0, n - train_window - test_window + 1, step):
        train = daily_returns[start:start + train_window]
        test = daily_returns[start + train_window:start + train_window + test_window]

        is_sharpe = compute_sharpe_ratio(train)
        oos_sharpe = compute_sharpe_ratio(test)

        windows.append(WFAWindow(
            train_start=start, train_end=start + train_window - 1,
            test_start=start + train_window, test_end=start + train_window + test_window - 1,
            in_sample_sharpe=is_sharpe,
            out_of_sample_sharpe=oos_sharpe,
            oos_positive=oos_sharpe > 0,
        ))

    if not windows:
        return WFAResult(windows=[], verdict="无有效窗口")

    is_sharpes = [w.in_sample_sharpe for w in windows]
    oos_sharpes = [w.out_of_sample_sharpe for w in windows]

    mean_is = float(np.mean(is_sharpes)) if is_sharpes else 0.0
    mean_oos = float(np.mean(oos_sharpes)) if oos_sharpes else 0.0
    oos_pos_ratio = sum(1 for w in windows if w.oos_positive) / len(windows)

    # Decay ratio: how much OOS performance degrades vs IS
    decay = (mean_is - mean_oos) / mean_is if mean_is > 0.01 else 0.0

    is_robust = decay < 0.4 and mean_oos > 0.5

    if is_robust and mean_oos > 1.0:
        verdict = "✅ 策略稳健 — 样本外Sharpe > 1.0 且衰减可控"
    elif is_robust:
        verdict = "⚠️ 策略可接受 — 衰减在合理范围内，但样本外Sharpe偏低"
    elif decay >= 0.4:
        verdict = "❌ 严重过拟合 — 样本外表现大幅衰减(>{:.0%})".format(decay)
    else:
        verdict = "❌ 策略无效 — 样本外无正收益"

    return WFAResult(
        windows=windows,
        mean_is_sharpe=mean_is,
        mean_oos_sharpe=mean_oos,
        oos_positive_ratio=oos_pos_ratio,
        decay_ratio=decay,
        is_robust=is_robust,
        verdict=verdict,
    )
