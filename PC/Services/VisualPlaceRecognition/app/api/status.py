"""Liveness and readiness endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

from app.schemas import HealthResponse, IndexStatusResponse


router = APIRouter(tags=["status"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report only that the HTTP process is alive."""
    return HealthResponse()


@router.get("/visual-index/status", response_model=IndexStatusResponse)
async def status(request: Request) -> IndexStatusResponse:
    """Report model, database, and index readiness without triggering inference."""
    descriptor = request.app.state.descriptor
    indexing = request.app.state.indexing_service
    repository = request.app.state.repository
    settings = request.app.state.settings
    snapshot = indexing.get_snapshot()
    last_rebuild_at = (
        datetime.fromisoformat(snapshot.last_rebuild_at) if snapshot.last_rebuild_at else None
    )
    return IndexStatusResponse(
        ready=bool(descriptor.model_loaded and snapshot.loaded),
        model_loaded=descriptor.model_loaded,
        descriptor_backend=descriptor.backend,
        descriptor_version=descriptor.version,
        descriptor_dimension=descriptor.dimension or snapshot.index.dimension,
        device=descriptor.device,
        index_type=snapshot.index.index_type,
        index_size=snapshot.index.size,
        index_loaded=snapshot.loaded,
        database_entries=repository.count_active(),
        index_version=snapshot.version,
        unknown_threshold=settings.unknown_threshold,
        ambiguous_margin=settings.ambiguous_margin,
        last_rebuild_at=last_rebuild_at,
        last_error=indexing.last_error or getattr(descriptor, "load_error", None),
    )
