from __future__ import annotations

from typing import Any, Dict, Optional

from src.fusion.utils import apply_bayesian_override
from src.normalizer import SignalNormalizer, L7_SIGNAL_NAMES, L7_POSITION
from src.reliability import (
    ConfidenceCalibrator,
    HallucinationDetector,
    ReliabilityConfig,
    probability_to_decision,
)


class BayesianFusionStrategy:
    def __init__(self, normalizer, logger, probability_k: float = 1.0,
                 ly_veto_threshold: float = 0.30):
        self.normalizer = normalizer
        self.logger = logger
        self.probability_k = probability_k
        self.ly_veto_threshold = ly_veto_threshold

    def fuse(
        self,
        stock_code: str,
        stock_name: str = "",
        lynx_signal: str = "观望",
        lynx_prob_up: float = 50.0,
        mindlynx_advice: str = "观望",
        mindlynx_score: int = 50,
        mindlynx_trend: Optional[str] = None,
        mindlynx_valid: bool = False,
        mindlynx_factor_baseline: Optional[float] = None,
        tradingagent_rating: str = "Hold",
        tradingagent_valid: bool = False,
        ta_is_stale: bool = False,
        ta_debate_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        lynx_normalized, lynx_valid = self.normalizer.normalize_lynx(
            lynx_prob_up
        )
        mindlynx_normalized = self.normalizer.normalize_mindlynx_score(
            mindlynx_score,
        )
        tradingagent_normalized = self.normalizer.normalize_tradingagent(
            tradingagent_rating, debate_state=ta_debate_state
        )

        p_ly = self.normalizer.to_probability(lynx_normalized, self.probability_k)
        p_ml = self.normalizer.to_probability(mindlynx_normalized, self.probability_k)
        p_at = self.normalizer.to_probability(tradingagent_normalized, self.probability_k)

        h_ly = HallucinationDetector.detect_ly()
        h_ml = HallucinationDetector.detect_ml(
            sentiment_score=mindlynx_score,
            factor_baseline=mindlynx_factor_baseline,
            operation_advice=mindlynx_advice,
            trend_prediction=mindlynx_trend,
        )
        h_at = HallucinationDetector.detect_at(debate_state=ta_debate_state)

        c_ly = ConfidenceCalibrator.calibrate_ly(lynx_prob_up)
        c_ml = ConfidenceCalibrator.calibrate_ml(float(mindlynx_score))
        c_at = ConfidenceCalibrator.calibrate_at(
            (ta_debate_state or {}).get("investment_agreement", 0.5)
        )

        w_ly = ReliabilityConfig.alpha("lynx_vnpy") * c_ly * (1.0 - h_ly)
        w_ml = ReliabilityConfig.alpha("mindlynx", stock_code=stock_code) * c_ml * (1.0 - h_ml)
        w_at = ReliabilityConfig.alpha("tradingagent") * c_at * (1.0 - h_at)

        if not mindlynx_valid:
            self.logger.info(f"[Bayesian][{stock_code}] mindlynx 不可用，ML 权重归零")
            w_ml = 0.0
        if not tradingagent_valid:
            self.logger.info(f"[Bayesian][{stock_code}] tradingagent 不可用，AT 权重归零")
            w_at = 0.0

        if ta_is_stale:
            w_at *= 0.7
            self.logger.info(f"[Bayesian][{stock_code}] TA 过期，权重降低 30%")

        total_w = w_ly + w_ml + w_at
        if total_w == 0:
            return {
                "stock_code": stock_code, "stock_name": stock_name,
                "valid": False, "message": "所有系统有效权重为零，无法融合",
                "fusion_mode": "bayesian",
            }

        p_fused = (w_ly * p_ly + w_ml * p_ml + w_at * p_at) / total_w
        p_final = apply_bayesian_override(p_ly, p_ml, p_at, p_fused)

        signal_label = probability_to_decision(p_final, {
            "strong_bullish": 0.88, "bullish": 0.73,
            "cautious_bullish": 0.62, "cautious_bearish": 0.38,
            "bearish": 0.27, "strong_bearish": 0.12,
        })

        position_map = L7_POSITION
        has_disagreement = ((p_ly - 0.50) * (p_at - 0.50) < -0.2
                           or (p_ly - 0.50) * (p_ml - 0.50) < -0.2
                           or (p_ml - 0.50) * (p_at - 0.50) < -0.2)
        position = position_map.get(signal_label, "0成")
        if has_disagreement:
            position = SignalNormalizer.cap_position_for_disagreement(position)

        return {
            "stock_code": stock_code, "stock_name": stock_name,
            "valid": True, "fusion_mode": "bayesian",
            "p_ly": round(p_ly, 3), "p_ml": round(p_ml, 3), "p_at": round(p_at, 3),
            "w_ly": round(w_ly, 3), "w_ml": round(w_ml, 3), "w_at": round(w_at, 3),
            "h_ly": h_ly, "h_ml": round(h_ml, 3), "h_at": round(h_at, 3),
            "c_ly": round(c_ly, 3), "c_ml": round(c_ml, 3), "c_at": round(c_at, 3),
            "p_fused": round(p_fused, 3), "p_final": round(p_final, 3),
            "has_disagreement": has_disagreement,
            "lynx_valid": lynx_valid,
            "mindlynx_valid": mindlynx_valid,
            "tradingagent_valid": tradingagent_valid,
            "ta_is_stale": ta_is_stale,
            "signal": signal_label,
            "signal_name": L7_SIGNAL_NAMES.get(signal_label, "中性/观望"),
            "position_advice": position,
        }
