"""Target-place arrival verification endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.image_io import read_upload_limited
from app.schemas import ErrorResponse, VerifyResponse


router = APIRouter(prefix="/visual-index", tags=["recognition"])


@router.post(
    "/verify",
    response_model=VerifyResponse,
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def verify(
    request: Request,
    target_place_id: Annotated[str, Form(min_length=1, max_length=255)],
    image: Annotated[UploadFile, File()],
) -> VerifyResponse:
    raw = await read_upload_limited(image, request.app.state.settings.max_image_bytes)
    loaded = request.app.state.image_loader.load_upload(raw, image.content_type)
    return request.app.state.recognition_service.verify(
        loaded.image,
        target_place_id,
    )
