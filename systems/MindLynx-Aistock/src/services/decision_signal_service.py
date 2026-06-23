"""Decision Signal Service.

Orchestrates extraction, persistence, and querying of decision signals.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.repositories.decision_signal_repo import DecisionSignalRepository
from src.services.decision_signal_extractor import extract_signals_from_opinions
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


class DecisionSignalService:
    """Business logic for decision signal extraction, storage, and retrieval."""

    def __init__(self, db_manager: DatabaseManager | None = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.repo = DecisionSignalRepository(self.db)

    # ---- Write ----

    def extract_and_save(
        self,
        opinions: list[Any],
        stock_code: str,
        stock_name: str,
        query_id: str,
        analysis_date: datetime | None = None,
    ) -> int:
        """Extract signal records from opinions and persist them.

        Args:
            opinions: List of ``AgentOpinion`` objects or plain dicts.
            stock_code: Stock identifier.
            stock_name: Human-readable stock name.
            query_id: Correlation ID for the analysis run.
            analysis_date: Override timestamp; defaults to now.

        Returns:
            Number of signal records saved.
        """
        signals = extract_signals_from_opinions(
            opinions=opinions,
            stock_code=stock_code,
            stock_name=stock_name,
            query_id=query_id,
            analysis_date=analysis_date,
        )
        if not signals:
            logger.debug("No signals extracted for %s (query_id=%s)", stock_code, query_id)
            return 0

        saved = self.repo.save_batch(signals)
        logger.info("Saved %d decision signal(s) for %s (query_id=%s)", saved, stock_code, query_id)
        return saved

    def save_from_agent_result(
        self,
        dashboard: dict[str, Any] | None,
        stock_code: str,
        stock_name: str,
        query_id: str,
    ) -> int:
        """Extract a consensus signal from an ``AgentResult`` dashboard and persist it.

        This is the primary hook for the pipeline — works even when individual
        skill opinions are not available (``AGENT_ARCH=single``).

        Args:
            dashboard: The ``dashboard`` field from ``AgentResult``.
            stock_code: Stock identifier.
            stock_name: Human-readable stock name.
            query_id: Correlation ID.

        Returns:
            Number of signal records saved (0 or 1).
        """
        if not dashboard:
            return 0

        decision_type = str(dashboard.get("decision_type", "hold") or "hold")
        # Map confidence_level text to a numeric value
        confidence_str = str(dashboard.get("confidence_level", "") or "")
        confidence = self._confidence_to_float(confidence_str)

        raw = dashboard.get("analysis_summary") or dashboard.get("key_points") or ""
        reasoning = str(raw)[:500] if raw else ""

        signal = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "skill_id": "consensus",
            "signal": decision_type,
            "confidence": confidence,
            "conditions_met": [],
            "conditions_missed": [],
            "score_adjustment": 0.0,
            "reasoning": reasoning,
            "analysis_date": datetime.now(),
            "query_id": query_id,
        }
        return self.repo.save_batch([signal])

    @staticmethod
    def _confidence_to_float(confidence_str: str) -> float:
        """Map localized confidence labels to 0.0-1.0."""
        mapping = {
            "very_high": 0.95,
            "high": 0.80,
            "medium": 0.60,
            "medium_high": 0.70,
            "medium_low": 0.45,
            "low": 0.30,
            "very_low": 0.15,
            "极高": 0.95,
            "高": 0.80,
            "中高": 0.70,
            "中": 0.60,
            "中低": 0.45,
            "低": 0.30,
            "极低": 0.15,
        }
        key = confidence_str.strip().lower()
        return mapping.get(key, 0.5)

    def save_signals_direct(
        self,
        signals: list[dict[str, Any]],
    ) -> int:
        """Persist pre-built signal dicts directly (bypass extraction).

        Each dict must be compatible with :class:`DecisionSignalCreate`.
        """
        if not signals:
            return 0
        return self.repo.save_batch(signals)

    # ---- Read ----

    def get_stock_signals(
        self,
        stock_code: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent signals for a stock, newest first."""
        return self.repo.get_by_stock(stock_code, limit=limit)

    def get_latest_signal(
        self,
        stock_code: str,
        skill_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the most recent signal for a stock, optionally filtered by skill."""
        return self.repo.get_latest(stock_code, skill_id=skill_id)

    def get_signal_summary(self, stock_code: str) -> dict[str, Any]:
        """Return aggregated signal statistics for a stock."""
        return self.repo.get_summary(stock_code)

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent signals across all stocks."""
        return self.repo.list_recent(limit=limit)
