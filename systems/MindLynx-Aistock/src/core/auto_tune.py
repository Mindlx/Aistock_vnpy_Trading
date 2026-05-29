"""Auto-tuner for semi-objective parameters.

Phase 1:
- Anchor threshold: finds optimal LLM-vs-factor divergence threshold
  and blend ratio by evaluating historical prediction accuracy.
- Factor label threshold: finds optimal composite score cutoff
  for "strong" / "neutral" / "weak" labels by maximizing
  forward return differentiation.

All tuners require minimum sample sizes and use Spearman IC as
the objective metric.

Usage:
    from src.core.auto_tune import AnchorTuner, FactorLabelTuner
    tuner = AnchorTuner()
    optimal = tuner.find_optimal(bias_data)
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_MIN_SAMPLES = 50
_DAMPING = 0.3
_MIN_IC_IMPROVEMENT = 0.005
_LAST_TUNE_FILE = "reports/factors/.last_auto_tune.json"


def _spearman_ic(x, y):
    xr = np.argsort(np.argsort(x)).astype(float)
    yr = np.argsort(np.argsort(y)).astype(float)
    if np.std(xr) < 1e-10 or np.std(yr) < 1e-10:
        return 0.0
    n = len(xr)
    d = xr - yr
    rho = 1.0 - (6.0 * np.sum(d**2)) / (n * (n**2 - 1.0))
    return float(rho) if not np.isnan(rho) else 0.0


@dataclass
class TuneResult:
    parameter: str
    old_value: Any
    new_value: Any
    ic_improvement: float
    sample_count: int


class AnchorTuner:
    candidate_thresholds = [10, 15, 20, 25, 30]
    candidate_ratios = [(0.5, 0.5), (0.55, 0.45), (0.6, 0.4), (0.65, 0.35), (0.7, 0.3)]

    def __init__(self, db_path: str = "data/stock_analysis.db"):
        self.db_path = db_path

    def find_optimal(self) -> list[TuneResult] | None:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT predicted_score, actual_forward_return FROM prediction_bias "
            "WHERE actual_forward_return IS NOT NULL ORDER BY id DESC LIMIT 200"
        ).fetchall()
        conn.close()

        if len(rows) < _MIN_SAMPLES:
            return None

        predictions = np.array([r[0] for r in rows])
        returns = np.array([r[1] for r in rows])

        baseline_ic = _spearman_ic(predictions, returns)

        last_tune = self._load_last()
        prev_threshold = last_tune.get("threshold", 20)
        prev_ratio = last_tune.get("ratio", (0.6, 0.4))

        best_threshold = prev_threshold
        best_ratio = prev_ratio
        best_ic = baseline_ic

        for t in self.candidate_thresholds:
            for llm_w, factor_w in self.candidate_ratios:
                blended = np.array(
                    [
                        (
                            p
                            if abs(p - self._factor_mapped(p)) <= t
                            else int(p * llm_w + self._factor_mapped(p) * factor_w)
                        )
                        for p in predictions
                    ]
                )
                ic = _spearman_ic(blended, returns)
                if ic > best_ic:
                    best_ic = ic
                    best_threshold = t
                    best_ratio = (llm_w, factor_w)

        ic_gain = best_ic - baseline_ic
        if ic_gain < _MIN_IC_IMPROVEMENT:
            return None

        smoothed_threshold = int(prev_threshold * (1 - _DAMPING) + best_threshold * _DAMPING)
        smoothed_llm_w = prev_ratio[0] * (1 - _DAMPING) + best_ratio[0] * _DAMPING
        smoothed_factor_w = 1.0 - smoothed_llm_w

        self._save_last(
            {"threshold": smoothed_threshold, "ratio": (round(smoothed_llm_w, 2), round(smoothed_factor_w, 2))}
        )

        results = []
        if abs(smoothed_threshold - prev_threshold) >= 3:
            results.append(
                TuneResult(
                    parameter="anchor_divergence_threshold",
                    old_value=prev_threshold,
                    new_value=smoothed_threshold,
                    ic_improvement=round(ic_gain, 4),
                    sample_count=len(rows),
                )
            )
        if abs(smoothed_llm_w - prev_ratio[0]) >= 0.03:
            results.append(
                TuneResult(
                    parameter="anchor_blend_ratio",
                    old_value=prev_ratio,
                    new_value=(round(smoothed_llm_w, 2), round(smoothed_factor_w, 2)),
                    ic_improvement=round(ic_gain, 4),
                    sample_count=len(rows),
                )
            )

        return results if results else None

    def _load_last(self) -> dict:
        import json

        if not os.path.exists(_LAST_TUNE_FILE):
            return {}
        try:
            with open(_LAST_TUNE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_last(self, data: dict):
        import json

        os.makedirs(os.path.dirname(_LAST_TUNE_FILE), exist_ok=True)
        with open(_LAST_TUNE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

    @staticmethod
    def _factor_mapped(llm_score):
        return int(50 + (llm_score - 50) * 0.8)


class FactorLabelTuner:
    candidate_thresholds = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]

    def __init__(self, db_path: str = "data/stock_analysis.db"):
        self.db_path = db_path

    def find_optimal(self) -> TuneResult | None:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT ah.sentiment_score FROM analysis_history ah "
            "WHERE ah.sentiment_score IS NOT NULL ORDER BY ah.id DESC LIMIT 200"
        ).fetchall()
        conn.close()

        if len(rows) < _MIN_SAMPLES:
            return None

        n = len(rows)
        scores = np.array([r[0] for r in rows])

        last_tune = self._load_last()
        prev_threshold = last_tune.get("label_threshold", 0.5)

        best_threshold = prev_threshold
        best_t_stat = 0.0

        for t in self.candidate_thresholds:
            factor_z = (scores - 50) / 16.67
            high_mask = factor_z > t
            low_mask = factor_z < -t
            high_count = high_mask.sum()
            low_count = low_mask.sum()

            if high_count < 5 or low_count < 5:
                continue

            high_mean = float(scores[high_mask].mean())
            low_mean = float(scores[low_mask].mean())
            high_std = float(scores[high_mask].std(ddof=1) or 1.0)
            low_std = float(scores[low_mask].std(ddof=1) or 1.0)

            t_stat = (high_mean - low_mean) / np.sqrt(high_std**2 / high_count + low_std**2 / low_count)
            if t_stat > best_t_stat:
                best_t_stat = float(t_stat)
                best_threshold = t

        smoothed = round(prev_threshold * (1 - _DAMPING) + best_threshold * _DAMPING, 2)
        if abs(smoothed - prev_threshold) < 0.03:
            return None

        self._save_last({"label_threshold": smoothed})
        return TuneResult(
            parameter="factor_label_threshold",
            old_value=prev_threshold,
            new_value=smoothed,
            ic_improvement=round(float(best_t_stat), 4),
            sample_count=n,
        )

    def _load_last(self) -> dict:
        import json

        if not os.path.exists(_LAST_TUNE_FILE):
            return {}
        try:
            with open(_LAST_TUNE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_last(self, data: dict):
        import json

        os.makedirs(os.path.dirname(_LAST_TUNE_FILE), exist_ok=True)
        with open(_LAST_TUNE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)


def auto_tune_if_ready(db_path: str = "data/stock_analysis.db") -> dict[str, Any] | None:
    at = AnchorTuner(db_path)
    flt = FactorLabelTuner(db_path)

    anchor_results = at.find_optimal()
    label_results = flt.find_optimal()

    if not anchor_results and not label_results:
        return None

    result: dict[str, Any] = {}
    if anchor_results:
        result["anchor"] = [{"parameter": r.parameter, "old": r.old_value, "new": r.new_value} for r in anchor_results]
    if label_results:
        result["label"] = {
            "parameter": label_results.parameter,
            "old": label_results.old_value,
            "new": label_results.new_value,
        }

    return result
