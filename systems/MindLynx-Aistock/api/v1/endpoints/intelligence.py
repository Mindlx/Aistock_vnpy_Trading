"""Intelligence API endpoints.

CRUD for RSS/Atom intelligence sources and fetched items.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.intelligence import (
    IntelligenceCreateDefaultsResponse,
    IntelligenceFetchRequest,
    IntelligenceFetchResponse,
    IntelligenceFetchStatus,
    IntelligenceItemListResponse,
    IntelligenceItemResponse,
    IntelligenceSourceCreateRequest,
    IntelligenceSourceItem,
    IntelligenceSourceListResponse,
    IntelligenceSourceTestRequest,
    IntelligenceSourceTestResponse,
    IntelligenceSourceUpdateRequest,
)
from src.services.intelligence_service import (
    IntelligenceFetchError,
    IntelligenceService,
    IntelligenceServiceError,
    IntelligenceSourceNotFoundError,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _bad_request(exc: Exception, *, error: str = "validation_error") -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": error, "message": str(exc)},
    )


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "not_found", "message": str(exc)},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: {str(exc)}"},
    )


# ---- Source CRUD ----


@router.post(
    "/sources",
    response_model=IntelligenceSourceItem,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Create intelligence source",
)
def create_source(request: IntelligenceSourceCreateRequest) -> IntelligenceSourceItem:
    service = IntelligenceService()
    try:
        return IntelligenceSourceItem(**service.create_source(request.model_dump()))
    except IntelligenceServiceError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except Exception as exc:
        raise _internal_error("Create intelligence source failed", exc)


@router.get(
    "/sources",
    response_model=IntelligenceSourceListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List intelligence sources",
)
def list_sources(
    enabled: bool | None = Query(None, description="Optional enabled filter"),
    source_type: str | None = Query(None, description="Optional source type filter (rss/atom)"),
    scope_type: str | None = Query(None, description="Optional scope type filter"),
    market: str | None = Query(None, description="Optional market filter (cn/hk/us)"),
) -> IntelligenceSourceListResponse:
    service = IntelligenceService()
    try:
        return IntelligenceSourceListResponse(
            **service.list_sources(
                enabled=enabled,
                source_type=source_type,
                scope_type=scope_type,
                market=market,
            )
        )
    except Exception as exc:
        raise _internal_error("List intelligence sources failed", exc)


@router.get(
    "/sources/{source_id}",
    response_model=IntelligenceSourceItem,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get intelligence source",
)
def get_source(source_id: int) -> IntelligenceSourceItem:
    service = IntelligenceService()
    try:
        return IntelligenceSourceItem(**service.get_source(source_id))
    except IntelligenceSourceNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Get intelligence source failed", exc)


@router.put(
    "/sources/{source_id}",
    response_model=IntelligenceSourceItem,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Update intelligence source",
)
def update_source(
    source_id: int,
    request: IntelligenceSourceUpdateRequest,
) -> IntelligenceSourceItem:
    service = IntelligenceService()
    try:
        payload = request.model_dump(exclude_unset=True)
        return IntelligenceSourceItem(**service.update_source(source_id, payload))
    except IntelligenceSourceNotFoundError as exc:
        raise _not_found(exc)
    except IntelligenceServiceError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except Exception as exc:
        raise _internal_error("Update intelligence source failed", exc)


@router.delete(
    "/sources/{source_id}",
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Delete intelligence source",
)
def delete_source(source_id: int) -> dict[str, bool]:
    service = IntelligenceService()
    try:
        if not service.delete_source(source_id):
            raise IntelligenceSourceNotFoundError(f"Intelligence source not found: {source_id}")
        return {"deleted": True}
    except IntelligenceSourceNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Delete intelligence source failed", exc)


# ---- Source extras ----


@router.post(
    "/sources/test",
    response_model=IntelligenceSourceTestResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Test a source URL without saving",
)
def test_source(request: IntelligenceSourceTestRequest) -> IntelligenceSourceTestResponse:
    service = IntelligenceService()
    try:
        return IntelligenceSourceTestResponse(**service.test_source_url(request.url))
    except IntelligenceServiceError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except Exception as exc:
        raise _internal_error("Test intelligence source failed", exc)


@router.post(
    "/sources/defaults",
    response_model=IntelligenceCreateDefaultsResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Create default built-in intelligence sources",
)
def create_default_sources() -> IntelligenceCreateDefaultsResponse:
    service = IntelligenceService()
    try:
        created, sources = service.create_default_sources()
        return IntelligenceCreateDefaultsResponse(
            created=created,
            sources=[IntelligenceSourceItem(**s) for s in sources],
        )
    except Exception as exc:
        raise _internal_error("Create default intelligence sources failed", exc)


# ---- Items ----


@router.get(
    "/items",
    response_model=IntelligenceItemListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List intelligence items",
)
def list_items(
    source_id: int | None = Query(None, description="Filter by source ID"),
    source_type: str | None = Query(None, description="Filter by source type"),
    scope_type: str | None = Query(None, description="Filter by scope type"),
    scope_value: str | None = Query(None, description="Filter by scope value"),
    market: str | None = Query(None, description="Filter by market"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> IntelligenceItemListResponse:
    service = IntelligenceService()
    try:
        rows, total = service.repo.list_items(
            source_id=source_id,
            source_type=source_type,
            scope_type=scope_type,
            scope_value=scope_value,
            market=market,
            page=page,
            page_size=page_size,
        )
        return IntelligenceItemListResponse(
            items=[IntelligenceItemResponse(**service.serialize_item(row)) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise _internal_error("List intelligence items failed", exc)


# ---- Fetch ----


@router.post(
    "/fetch",
    response_model=IntelligenceFetchResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Fetch intelligence from one or all sources",
)
def fetch_sources(request: IntelligenceFetchRequest) -> IntelligenceFetchResponse:
    service = IntelligenceService()
    try:
        if request.source_id is not None:
            result = service.fetch_source(request.source_id)
            return IntelligenceFetchResponse(results=[IntelligenceFetchStatus(**result)])
        results = service.fetch_all_enabled()
        return IntelligenceFetchResponse(
            results=[IntelligenceFetchStatus(**r) for r in results]
        )
    except IntelligenceSourceNotFoundError as exc:
        raise _not_found(exc)
    except IntelligenceFetchError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except Exception as exc:
        raise _internal_error("Fetch intelligence sources failed", exc)
