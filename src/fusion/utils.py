from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def detect_disagreement(
    lynx_score: float, mindlynx_score: float, tradingagent_score: float,
    lynx_valid: bool, mindlynx_valid: bool, tradingagent_valid: bool,
) -> Tuple[bool, float, int]:
    scores = []
    if lynx_valid:
        scores.append(("lynx", lynx_score))
    if mindlynx_valid:
        scores.append(("mindlynx", mindlynx_score))
    if tradingagent_valid:
        scores.append(("tradingagent", tradingagent_score))

    if len(scores) < 2:
        return False, 0.0, -1

    has_bullish = any(s > 0.5 for _, s in scores)
    has_bearish = any(s < -0.5 for _, s in scores)
    disagreement = has_bullish and has_bearish

    disagreement_score = 0.0
    ml_minority = 0
    if disagreement:
        raw = [s for _, s in scores]
        mean = sum(raw) / len(raw)
        variance = sum((s - mean) ** 2 for s in raw) / len(raw)
        disagreement_score = math.sqrt(variance)

        ml_s = next((s for name, s in scores if name == "mindlynx"), 0.0)
        others = [s for name, s in scores if name != "mindlynx"]
        majority_dir = sum(1 for s in others if s > 0.5) - sum(1 for s in others if s < -0.5)
        if abs(majority_dir) >= len(others):
            ml_dir = 1 if ml_s > 0.5 else (-1 if ml_s < -0.5 else 0)
            if ml_dir != 0 and ml_dir != (1 if majority_dir > 0 else -1):
                ml_minority = 1

    return disagreement, disagreement_score, ml_minority


def compute_uncertainty_penalty(disagreement_score: float) -> float:
    if disagreement_score <= 0.5:
        return 0.0
    return min(2.0, max(0.0, (disagreement_score - 0.5) * 1.2))


def compute_adjusted_weights(
    lynx_valid: bool, mindlynx_valid: bool, tradingagent_valid: bool,
    weights: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, float], int, bool]:
    w = weights or {"lynx": 0.35, "mindlynx": 0.35, "tradingagent": 0.30}
    adjusted = {}
    if lynx_valid:
        adjusted["lynx"] = w["lynx"]
    if mindlynx_valid:
        adjusted["mindlynx"] = w["mindlynx"]
    if tradingagent_valid:
        adjusted["tradingagent"] = w["tradingagent"]

    valid_count = len(adjusted)
    if valid_count == 0:
        return {}, 0, True

    total = sum(adjusted.values())
    for k in adjusted:
        adjusted[k] /= total

    return adjusted, valid_count, len(adjusted) < 3


def get_final_decision(score: float, disagreement: bool = False,
                       thresholds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    from src.normalizer import (
        L7_POSITION, L7_SIGNAL_NAMES, L7_THRESHOLDS as DEFAULT_THRESHOLDS,
    )
    t = thresholds or DEFAULT_THRESHOLDS
    if score > t["strong_bullish"]:
        signal = "strong_bullish"
    elif score > t["bullish"]:
        signal = "bullish"
    elif score > t["cautious_bullish"]:
        signal = "cautious_bullish"
    elif score > t["cautious_bearish"]:
        signal = "neutral"
    elif score > t["bearish"]:
        signal = "cautious_bearish"
    elif score > t["strong_bearish"]:
        signal = "bearish"
    else:
        signal = "strong_bearish"

    name = L7_SIGNAL_NAMES.get(signal, "中性/持有")
    position = L7_POSITION.get(signal, "0成")

    return {
        "signal": signal, "name": name,
        "position": position,
        "disagreement_capped": disagreement,
    }


def apply_bayesian_override(
    p_ly: float, p_ml: float, p_at: float, p_fused: float,
) -> float:
    ly_conviction = abs(p_ly - 0.50)
    if ly_conviction <= 0.30:
        return p_fused

    def _direction(p: float) -> int:
        return 1 if p > 0.55 else (-1 if p < 0.45 else 0)

    ly_dir = _direction(p_ly)
    fused_dir = _direction(p_fused)
    if ly_dir == 0 or ly_dir == fused_dir:
        return p_fused

    ml_dir = _direction(p_ml)
    at_dir = _direction(p_at)

    if ml_dir == at_dir == -ly_dir:
        return 0.40 * p_ly + 0.60 * p_fused
    elif ml_dir == ly_dir:
        return 0.80 * p_ly + 0.20 * p_fused
    elif at_dir == ly_dir:
        return 0.70 * p_ly + 0.30 * p_fused
    else:
        return p_fused
