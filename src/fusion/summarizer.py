from __future__ import annotations

from typing import Any, Dict, List


class PortfolioSummarizer:
    def summarize(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(results)
        valid = [r for r in results if r.get("valid")]
        bullish = sum(1 for r in valid if r.get("signal") in (
            "strong_bullish", "bullish", "cautious_bullish"))
        bearish = sum(1 for r in valid if r.get("signal") in (
            "strong_bearish", "bearish", "cautious_bearish"))
        neutral = len(valid) - bullish - bearish
        degraded = sum(1 for r in results if r.get("is_degraded"))

        return {
            "total": total,
            "valid": len(valid),
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "degraded": degraded,
            "bullish_ratio": round(bullish / len(valid), 2) if valid else 0,
        }

    def _direction_bias(self, scores: list, threshold: float = 0.5) -> dict:
        bullish = sum(1 for s in scores if s > threshold)
        bearish = sum(1 for s in scores if s < -threshold)
        return {"bullish": bullish, "bearish": bearish, "neutral": len(scores) - bullish - bearish}
