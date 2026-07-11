from __future__ import annotations

from typing import Any, Dict, Optional

from src.fusion.utils import (
    compute_adjusted_weights,
    compute_uncertainty_penalty,
    detect_disagreement,
    get_final_decision,
)


class LinearFusionStrategy:
    def __init__(self, normalizer, logger, weights: Dict[str, float],
                 sentiment_threshold_bull: int = 52,
                 sentiment_threshold_bear: int = 49):
        self.normalizer = normalizer
        self.logger = logger
        self.weights = weights
        self.sentiment_threshold_bull = sentiment_threshold_bull
        self.sentiment_threshold_bear = sentiment_threshold_bear

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
        tradingagent_rating: str = "Hold",
        tradingagent_valid: bool = False,
        ta_is_stale: bool = False,
        ta_debate_state: Optional[Dict[str, Any]] = None,
        alpha158_l7: Optional[float] = None,
    ) -> Dict[str, Any]:
        lynx_normalized, lynx_valid = self.normalizer.normalize_lynx(
            lynx_signal, lynx_prob_up
        )

        if alpha158_l7 is not None and lynx_valid:
            blend = 0.10
            lynx_normalized = lynx_normalized * (1 - blend) + float(alpha158_l7) * blend
            self.logger.info(f"[{stock_code}] alpha158增强ly: a158={float(alpha158_l7):.2f} ly→{lynx_normalized:.2f}")

        if not mindlynx_valid:
            mindlynx_normalized = 0.0
            mindlynx_score_normalized = 0.0
        else:
            mindlynx_normalized = self.normalizer.normalize_mindlynx(
                mindlynx_advice, mindlynx_score, mindlynx_trend
            )
            mindlynx_score_normalized = self.normalizer.normalize_mindlynx_score(
                mindlynx_score,
                threshold_bull=self.sentiment_threshold_bull,
                threshold_bear=self.sentiment_threshold_bear,
            )
            mindlynx_normalized = mindlynx_score_normalized * 0.8 + mindlynx_normalized * 0.2

        if not tradingagent_valid:
            tradingagent_normalized = 0.0
        else:
            tradingagent_normalized = self.normalizer.normalize_tradingagent(
                tradingagent_rating, debate_state=ta_debate_state
            )

        ta_stale_penalty = 0.0
        if ta_is_stale:
            ta_stale_penalty = 0.30
            self.logger.info(f"[{stock_code}] TA 数据为昨日结果，权重降低 {ta_stale_penalty*100:.0f}%")

        has_disagreement, disagreement_score, ml_minority = detect_disagreement(
            lynx_normalized, mindlynx_normalized, tradingagent_normalized,
            lynx_valid, mindlynx_valid, tradingagent_valid,
        )
        uncertainty_penalty = compute_uncertainty_penalty(disagreement_score)

        ml_minority_boost = 0.0
        if has_disagreement and ml_minority == 1:
            ml_minority_boost = min(0.3, disagreement_score * 0.15)

        adjusted_weights, valid_count, is_degraded = compute_adjusted_weights(
            lynx_valid, mindlynx_valid, tradingagent_valid, self.weights,
        )

        if ta_is_stale and "tradingagent" in adjusted_weights:
            ta_weight = adjusted_weights["tradingagent"]
            penalty = ta_weight * ta_stale_penalty
            adjusted_weights["tradingagent"] = ta_weight - penalty
            others = [k for k in adjusted_weights if k != "tradingagent"]
            if others:
                split = penalty / len(others)
                for k in others:
                    adjusted_weights[k] += split

        if valid_count == 0:
            return {
                "stock_code": stock_code, "stock_name": stock_name,
                "valid": False, "message": "所有系统均无效，无法生成信号",
                "is_degraded": True,
            }

        normalized_scores = {}
        if "lynx" in adjusted_weights:
            normalized_scores["lynx"] = lynx_normalized
        if "mindlynx" in adjusted_weights:
            normalized_scores["mindlynx"] = mindlynx_normalized
        if "tradingagent" in adjusted_weights:
            normalized_scores["tradingagent"] = tradingagent_normalized

        fusion_score = sum(
            normalized_scores[sys] * adjusted_weights[sys]
            for sys in normalized_scores
        )

        disagreement_capped = has_disagreement and disagreement_score > 0.5

        if ml_minority_boost > 0:
            self.logger.info(
                f"[{stock_code}] 分歧时ML少数方增强: +{ml_minority_boost:.2f} "
                f"(分歧分数={disagreement_score:.2f})"
            )
            fusion_score += ml_minority_boost
            fusion_score = max(-3.0, min(3.0, fusion_score))

        final = get_final_decision(fusion_score, has_disagreement)

        result = {
            "stock_code": stock_code, "stock_name": stock_name,
            "valid": True, "is_degraded": is_degraded,
            "degraded_info": f"{valid_count}/3 系统有效" if is_degraded else "",
            "has_disagreement": has_disagreement,
            "disagreement_score": round(disagreement_score, 3),
            "uncertainty_penalty": round(uncertainty_penalty, 3),
            "lynx_score": round(lynx_normalized, 3),
            "lynx_valid": lynx_valid,
            "mindlynx_score": round(mindlynx_normalized, 3),
            "mindlynx_valid": mindlynx_valid,
            "tradingagent_score": round(tradingagent_normalized, 3),
            "tradingagent_valid": tradingagent_valid,
            "fusion_score": round(fusion_score, 3),
            "signal": final["signal"],
            "signal_name": final["name"],
            "position_advice": final["position"],
            "disagreement_capped": disagreement_capped,
            "ta_is_stale": ta_is_stale,
            "ta_stale_penalty": ta_stale_penalty,
        }

        self.logger.record_decision(
            stock_code=stock_code, stock_name=stock_name,
            lynx_score=lynx_normalized, lynx_valid=lynx_valid,
            mindlynx_score=mindlynx_normalized, mindlynx_valid=mindlynx_valid,
            tradingagent_score=tradingagent_normalized,
            tradingagent_valid=tradingagent_valid,
            fusion_score=fusion_score,
            final_signal=final["signal"],
            position_advice=final["position"],
            is_degraded=is_degraded,
            has_disagreement=has_disagreement,
            fusion_mode="linear",
        )

        return result
