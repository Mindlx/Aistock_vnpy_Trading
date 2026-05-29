"""Uncertainty quantification via Block Bootstrap.

Computes factor score distributions using block bootstrap resampling
to quantify data uncertainty. Used to produce confidence intervals
and robustness labels for composite factor scores.

Usage:
    from src.core.uncertainty import UncertaintyQuantifier
    uq = UncertaintyQuantifier()
    dist = uq.compute_distribution(code, daily_data)
    print(f"Score: {dist['mean']:.1f} ± {dist['std']:.1f} [{dist['ci_95'][0]:.0f}-{dist['ci_95'][1]:.0f}]")
"""

from __future__ import annotations

import logging
import random
from typing import Any

import numpy as np

from src.core.factor_engine import CORE_FACTORS, FactorEngine

logger = logging.getLogger(__name__)

DEFAULT_N_BOOTSTRAP = 200   # sufficient for <1% CI error (was 50)
DEFAULT_BLOCK_SIZE = 5       # smaller blocks → more diversity (was 10)

SCALABLE_FACTOR_NAMES = {
    "momentum_reversal",
    "momentum_spread",
    "volume_trend",
    "volume_acceleration",
    "low_volatility",
    "turnover_sentiment",
    "price_position",
}


def _block_bootstrap(data: list[dict], block_size: int) -> list[dict]:
    """Block bootstrap for time series data.

    Samples non-overlapping blocks of consecutive rows with replacement
    to preserve local temporal structure. Each bootstrap sample has the
    same length as the original data.
    """
    n = len(data)
    if n < block_size:
        return data[:]
    n_blocks = n // block_size
    sampled: list[dict] = []
    while len(sampled) < n:
        start = random.randint(0, n - block_size)
        sampled.extend(data[start : start + block_size])
    return sampled[:n]


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(values, p))


class UncertaintyQuantifier:
    """Compute factor score distributions via block bootstrap."""

    def __init__(self, n_bootstrap: int = DEFAULT_N_BOOTSTRAP, block_size: int = DEFAULT_BLOCK_SIZE):
        self.n_bootstrap = n_bootstrap
        self.block_size = block_size
        self.engine = FactorEngine(factors=CORE_FACTORS)

    def compute_distribution(self, code: str, daily_data: list[dict]) -> dict[str, Any]:
        samples: list[float] = []
        for _ in range(self.n_bootstrap):
            bs_data = _block_bootstrap(daily_data, self.block_size)
            result = self.engine.compute_for_stock(code, bs_data)
            samples.append(result.composite_score)

        mean_val = float(np.mean(samples))
        std_val = float(np.std(samples, ddof=1)) if len(samples) > 1 else 0.0
        ci_low = _percentile(samples, 2.5)
        ci_high = _percentile(samples, 97.5)

        if std_val < 5:
            robustness = "high"
        elif std_val < 15:
            robustness = "medium"
        else:
            robustness = "low"

        return {
            "mean": round(mean_val, 2),
            "std": round(std_val, 2),
            "ci_95": [round(ci_low, 1), round(ci_high, 1)],
            "robustness": robustness,
            "n_samples": self.n_bootstrap,
        }

    def classify_ood(self, regime_label: str, recent_regimes: list[str], window: int = 30) -> dict[str, Any]:
        frequency = recent_regimes.count(regime_label) / max(len(recent_regimes), 1)
        threshold = 0.15
        is_ood = frequency < threshold
        return {
            "is_ood": is_ood,
            "frequency": round(frequency, 2),
            "threshold": threshold,
            "warning": (
                (f"当前市场状态({regime_label})在过去{window}天内出现{int(frequency * 100)}%，历史规律参考价值有限")
                if is_ood
                else None
            ),
        }
