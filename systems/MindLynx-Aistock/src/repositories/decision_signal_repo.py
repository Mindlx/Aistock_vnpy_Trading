"""Decision Signal repository.

SQLAlchemy data-access layer for the DecisionSignal model.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import and_, desc, func, select

from src.storage import DECISION_SIGNAL_FIELDS_JSON, DecisionSignal, DatabaseManager

logger = logging.getLogger(__name__)


def _serialize_row(row: DecisionSignal) -> dict[str, Any]:
    """Convert an ORM row to a plain dict, deserializing JSON fields."""
    conditions_met: list[str] = []
    conditions_missed: list[str] = []
    try:
        if row.conditions_met:
            conditions_met = json.loads(row.conditions_met) if isinstance(row.conditions_met, str) else row.conditions_met
    except (json.JSONDecodeError, TypeError):
        conditions_met = []
    try:
        if row.conditions_missed:
            conditions_missed = (
                json.loads(row.conditions_missed) if isinstance(row.conditions_missed, str) else row.conditions_missed
            )
    except (json.JSONDecodeError, TypeError):
        conditions_missed = []

    return {
        "id": row.id,
        "stock_code": row.stock_code,
        "stock_name": row.stock_name or "",
        "skill_id": row.skill_id,
        "signal": row.signal,
        "confidence": float(row.confidence or 0.0),
        "conditions_met": conditions_met,
        "conditions_missed": conditions_missed,
        "score_adjustment": float(row.score_adjustment or 0.0),
        "reasoning": row.reasoning or "",
        "analysis_date": row.analysis_date,
        "query_id": row.query_id or "",
        "created_at": row.created_at,
    }


def _prepare_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Prepare fields for DB insertion, serializing JSON columns."""
    fields = dict(item)
    # Serialize list fields to JSON
    for json_field in DECISION_SIGNAL_FIELDS_JSON:
        val = fields.get(json_field)
        if isinstance(val, list):
            fields[json_field] = json.dumps(val, ensure_ascii=False)
        elif val is None:
            fields[json_field] = "[]"
    # Ensure analysis_date
    if fields.get("analysis_date") is None:
        fields["analysis_date"] = datetime.now()
    return fields


class DecisionSignalRepository:
    """DB access layer for decision signals."""

    def __init__(self, db_manager: DatabaseManager | None = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ---- Write ----

    def save_batch(self, items: list[dict[str, Any]]) -> int:
        """Insert multiple signal records in a single transaction.

        Returns the number of rows inserted.
        """
        if not items:
            return 0
        with self.db.get_session() as session:
            rows = []
            for item in items:
                fields = _prepare_fields(item)
                rows.append(DecisionSignal(**fields))
            session.add_all(rows)
            session.commit()
            logger.debug("Saved %d decision signal(s)", len(rows))
            return len(rows)

    # ---- Read ----

    def get_by_stock(self, stock_code: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent signals for a stock, newest first."""
        with self.db.get_session() as session:
            rows = (
                session.execute(
                    select(DecisionSignal)
                    .where(DecisionSignal.stock_code == stock_code)
                    .order_by(desc(DecisionSignal.analysis_date), desc(DecisionSignal.id))
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_serialize_row(r) for r in rows]

    def get_latest(
        self,
        stock_code: str,
        skill_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the latest signal for a stock, optionally filtered by skill."""
        conditions = [DecisionSignal.stock_code == stock_code]
        if skill_id:
            conditions.append(DecisionSignal.skill_id == skill_id)

        with self.db.get_session() as session:
            row = (
                session.execute(
                    select(DecisionSignal)
                    .where(and_(*conditions))
                    .order_by(desc(DecisionSignal.analysis_date), desc(DecisionSignal.id))
                    .limit(1)
                )
                .scalars()
                .first()
            )
            return _serialize_row(row) if row else None

    def get_summary(self, stock_code: str) -> dict[str, Any]:
        """Aggregate signal statistics for a stock."""
        with self.db.get_session() as session:
            # Total count
            total = (
                session.execute(
                    select(func.count(DecisionSignal.id))
                    .select_from(DecisionSignal)
                    .where(DecisionSignal.stock_code == stock_code)
                ).scalar()
                or 0
            )

            # Signal type counts
            signal_counts_rows = session.execute(
                select(DecisionSignal.signal, func.count(DecisionSignal.id).label("cnt"))
                .where(DecisionSignal.stock_code == stock_code)
                .group_by(DecisionSignal.signal)
            ).fetchall()
            signal_counts: dict[str, int] = {}
            for sig, cnt in signal_counts_rows:
                signal_counts[str(sig)] = int(cnt)

            # Average confidence
            avg_conf = (
                session.execute(
                    select(func.avg(DecisionSignal.confidence))
                    .select_from(DecisionSignal)
                    .where(DecisionSignal.stock_code == stock_code)
                ).scalar()
                or 0.0
            )

            # Latest signal
            latest = (
                session.execute(
                    select(DecisionSignal)
                    .where(DecisionSignal.stock_code == stock_code)
                    .order_by(desc(DecisionSignal.analysis_date), desc(DecisionSignal.id))
                    .limit(1)
                )
                .scalars()
                .first()
            )

            # Per-skill latest snapshot
            latest_per_skill = (
                session.execute(
                    select(DecisionSignal)
                    .where(DecisionSignal.stock_code == stock_code)
                    .order_by(desc(DecisionSignal.analysis_date), desc(DecisionSignal.id))
                )
                .scalars()
                .all()
            )
            seen_skills: set[str] = set()
            skill_breakdown: list[dict[str, Any]] = []
            for r in latest_per_skill:
                if r.skill_id not in seen_skills:
                    seen_skills.add(r.skill_id)
                    skill_breakdown.append(
                        {
                            "skill_id": r.skill_id,
                            "signal": r.signal,
                            "confidence": float(r.confidence or 0.0),
                            "analysis_date": r.analysis_date,
                        }
                    )

            return {
                "stock_code": stock_code,
                "stock_name": latest.stock_name or "" if latest else "",
                "total_signals": int(total),
                "signal_counts": signal_counts,
                "avg_confidence": round(float(avg_conf), 4),
                "latest_signal": latest.signal if latest else None,
                "latest_confidence": float(latest.confidence) if latest else None,
                "latest_skill_id": latest.skill_id if latest else None,
                "latest_analysis_date": latest.analysis_date if latest else None,
                "skill_breakdown": skill_breakdown,
            }

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent signals across all stocks."""
        with self.db.get_session() as session:
            rows = (
                session.execute(
                    select(DecisionSignal)
                    .order_by(desc(DecisionSignal.created_at))
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_serialize_row(r) for r in rows]
