"""Whole-index visual place search endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.image_io import read_upload_limited
from app.schemas import ErrorResponse, SearchResponse


router = APIRouter(prefix="/visual-index", tags=["recognition"])


@router.post(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def search(
    request: Request,
    image: Annotated[UploadFile, File()],
    top_k: Annotated[int | None, Form(ge=1)] = None,
) -> SearchResponse:
    raw = await read_upload_limited(image, request.app.state.settings.max_image_bytes)
    loaded = request.app.state.image_loader.load_upload(raw, image.content_type)
    return request.app.state.recognition_service.search(
        loaded.image,
        top_k,
    )
