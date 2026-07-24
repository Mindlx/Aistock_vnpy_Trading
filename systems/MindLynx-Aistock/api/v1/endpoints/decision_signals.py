"""Decision Signal API endpoints.

Query endpoints for stored skill-level decision signals.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.decision_signals import (
    DecisionSignalListResponse,
    DecisionSignalResponse,
    DecisionSignalSummaryResponse,
)
from api.v1.schemas.common import ErrorResponse
from src.services.decision_signal_service import DecisionSignalService

logger = logging.getLogger(__name__)

router = APIRouter()


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "not_found", "message": message},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: {str(exc)}"},
    )


@router.get(
    "/stock/{code}",
    response_model=DecisionSignalListResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List decision signals for a stock",
)
def list_stock_signals(
    code: str,
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
) -> DecisionSignalListResponse:
    """Return recent decision signals for a stock, newest first."""
    service = DecisionSignalService()
    try:
        raw = service.get_stock_signals(code, limit=limit)
        items = [DecisionSignalResponse(**s) for s in raw]
        return DecisionSignalListResponse(items=items, total=len(items))
    except Exception as exc:
        raise _internal_error(f"Failed to fetch signals for {code}", exc)


@router.get(
    "/stock/{code}/summary",
    response_model=DecisionSignalSummaryResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Aggregated signal summary for a stock",
)
def stock_signal_summary(
    code: str,
) -> DecisionSignalSummaryResponse:
    """Return aggregated statistics (counts by signal, avg confidence, latest)."""
    service = DecisionSignalService()
    try:
        summary = service.get_signal_summary(code)
        return DecisionSignalSummaryResponse(**summary)
    except Exception as exc:
        raise _internal_error(f"Failed to compute signal summary for {code}", exc)


@router.get(
    "/latest",
    response_model=DecisionSignalListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Recent signals across all stocks",
)
def list_recent_signals(
    limit: int = Query(100, ge=1, le=500, description="Max records to return"),
) -> DecisionSignalListResponse:
    """Return the most recent decision signals across all stocks."""
    service = DecisionSignalService()
    try:
        raw = service.list_recent(limit=limit)
        items = [DecisionSignalResponse(**s) for s in raw]
        return DecisionSignalListResponse(items=items, total=len(items))
    except Exception as exc:
        raise _internal_error("Failed to fetch recent signals", exc)


@router.get("/outcomes/stats")
def get_outcomes_stats():
    return {"total": 0, "hit": 0, "miss": 0, "neutral": 0, "by_stock": []}


@router.post("/reassess")
def reassess_signals():
    return {"success": True, "reassessed": 0}


@router.post("/outcomes/run")
def run_outcomes():
    return {"success": True, "processed": 0}


@router.get("")
def list_signals():
    return {"items": [], "total": 0}


@router.get("/outcomes")
def list_outcomes():
    return {"items": [], "total": 0}


@router.get("/{signal_id}/outcomes")
def get_signal_outcomes(signal_id: int):
    return {"signal_id": signal_id, "hit": 0, "miss": 0, "neutral": 0}
