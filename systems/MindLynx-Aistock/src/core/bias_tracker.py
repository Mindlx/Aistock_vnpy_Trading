"""Prediction bias tracking for LLM calibration.

Tracks prediction outcomes to identify systematic biases (bullish/bearish,
recency, overconfidence). Requires ~30+ predictions for statistical
significance.

Usage:
    from src.core.bias_tracker import BiasTracker
    tracker = BiasTracker()
    tracker.record(code, predicted_score, actual_return, confidence, news_sentiment)
    report = tracker.generate_report()
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/stock_analysis.db"
SCORE_TO_ZSCALE = 1.0 / 16.67

CONFIDENCE_MAP = {
    "high": 0.9,
    "medium-high": 0.75,
    "medium": 0.5,
    "medium-low": 0.35,
    "low": 0.2,
}


class BiasTracker:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prediction_bias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(10) NOT NULL,
                predicted_score INTEGER NOT NULL,
                actual_forward_return FLOAT,
                confidence_level VARCHAR(16),
                news_sentiment_score FLOAT,
                forward_horizon_days INTEGER DEFAULT 5,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        conn.commit()
        conn.close()

    def record(
        self,
        code: str,
        predicted_score: int,
        actual_forward_return: float | None = None,
        confidence_level: str = "medium",
        news_sentiment_score: float | None = None,
        forward_horizon_days: int = 5,
    ):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO prediction_bias (code, predicted_score, actual_forward_return, "
            "confidence_level, news_sentiment_score, forward_horizon_days) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                code,
                predicted_score,
                actual_forward_return,
                confidence_level,
                news_sentiment_score,
                forward_horizon_days,
            ),
        )
        conn.commit()
        conn.close()

    def generate_report(self, min_samples: int = 30) -> dict | None:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT predicted_score, actual_forward_return, confidence_level, "
            "news_sentiment_score FROM prediction_bias "
            "WHERE actual_forward_return IS NOT NULL "
            "ORDER BY id DESC LIMIT 200"
        ).fetchall()
        conn.close()

        if len(rows) < min_samples:
            return None

        n = len(rows)
        predicted_z = [(r[0] - 50) * SCORE_TO_ZSCALE for r in rows]
        actual_z = [(r[1] - 0) / 0.03 if r[1] != 0 else 0 for r in rows]
        confidences = [CONFIDENCE_MAP.get(r[2] or "medium", 0.5) for r in rows]
        news_scores = [r[3] if r[3] else 0 for r in rows]

        errors = [p - a for p, a in zip(predicted_z, actual_z)]
        bullish_bias = sum(1 for e in errors if e > 0.3) / n
        bearish_bias = sum(1 for e in errors if e < -0.3) / n

        import math

        news_corr = 0.0
        valid_news = [(ns, e) for ns, e in zip(news_scores, errors) if ns != 0]
        if len(valid_news) > 5:
            nv = len(valid_news)
            sum_ns = sum(ns for ns, _ in valid_news)
            sum_e = sum(e for _, e in valid_news)
            sum_ns2 = sum(ns * ns for ns, _ in valid_news)
            sum_e2 = sum(e * e for _, e in valid_news)
            sum_nse = sum(ns * e for ns, e in valid_news)
            denom = math.sqrt((nv * sum_ns2 - sum_ns**2) * (nv * sum_e2 - sum_e**2))
            if denom > 1e-10:
                news_corr = (nv * sum_nse - sum_ns * sum_e) / denom

        overconfidence = sum(abs(e) for e in errors) / max(sum(confidences), 1)

        return {
            "sample_count": n,
            "bullish_bias": round(bullish_bias, 3),
            "bearish_bias": round(bearish_bias, 3),
            "recency_bias": round(news_corr, 3),
            "overconfidence_ratio": round(overconfidence, 3),
            "interpretation": _interpret(bullish_bias, bearish_bias, news_corr),
            "generated_at": datetime.now().isoformat(),
        }


def _interpret(bullish: float, bearish: float, recency: float) -> str:
    parts = []
    if bullish > 0.15:
        parts.append(f"模型有轻微看多倾向(看多偏差{bullish:.0%})")
    if bearish > 0.15:
        parts.append(f"模型有轻微看空倾向(看空偏差{bearish:.0%})")
    if abs(recency) > 0.25:
        direction = "强" if recency > 0 else "弱"
        parts.append(f"新闻近因偏差{direction}(r={recency:.2f})")
    if not parts:
        parts.append("未检测到显著系统性偏差")
    return "；".join(parts)
