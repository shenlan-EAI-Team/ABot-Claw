"""Reference image create, update, delete, and full rebuild endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, Response, UploadFile, status

from app.image_io import read_upload_limited
from app.schemas import (
    ErrorResponse,
    ImageIndexCreateRequest,
    ImageIndexResponse,
    ImageIndexUpdateRequest,
    RebuildResponse,
)


router = APIRouter(prefix="/visual-index", tags=["visual-index"])
ERROR_RESPONSES = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@router.post(
    "/images",
    response_model=ImageIndexResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def create_image_index(
    payload: ImageIndexCreateRequest,
    request: Request,
    response: Response,
) -> ImageIndexResponse:
    result = request.app.state.indexing_service.create_from_source(
        place_id=payload.place_id,
        image_id=payload.image_id,
        image_url=payload.image_url,
        expected_sha256=payload.image_sha256,
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return result


@router.post(
    "/images/upload",
    response_model=ImageIndexResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def upload_image_index(
    request: Request,
    response: Response,
    place_id: Annotated[str, Form(min_length=1, max_length=255)],
    image_id: Annotated[str, Form(min_length=1, max_length=255)],
    image: Annotated[UploadFile, File()],
    image_sha256: Annotated[str | None, Form(pattern=r"^[0-9a-fA-F]{64}$")] = None,
) -> ImageIndexResponse:
    raw = await read_upload_limited(image, request.app.state.settings.max_image_bytes)
    loaded = request.app.state.image_loader.load_upload(raw, image.content_type)
    result = request.app.state.indexing_service.create_from_loaded(
        place_id=place_id,
        image_id=image_id,
        image_url=None,
        loaded=loaded,
        expected_sha256=image_sha256,
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return result


@router.put(
    "/images/{image_id}",
    response_model=ImageIndexResponse,
    responses=ERROR_RESPONSES,
)
async def update_image_index(
    image_id: str,
    payload: ImageIndexUpdateRequest,
    request: Request,
) -> ImageIndexResponse:
    return request.app.state.indexing_service.update_from_source(
        image_id=image_id,
        place_id=payload.place_id,
        image_url=payload.image_url,
        expected_sha256=payload.image_sha256,
    )


@router.put(
    "/images/{image_id}/upload",
    response_model=ImageIndexResponse,
    responses=ERROR_RESPONSES,
)
async def update_uploaded_image_index(
    image_id: str,
    request: Request,
    image: Annotated[UploadFile, File()],
    place_id: Annotated[str | None, Form(min_length=1, max_length=255)] = None,
    image_sha256: Annotated[str | None, Form(pattern=r"^[0-9a-fA-F]{64}$")] = None,
) -> ImageIndexResponse:
    raw = await read_upload_limited(image, request.app.state.settings.max_image_bytes)
    loaded = request.app.state.image_loader.load_upload(raw, image.content_type)
    return request.app.state.indexing_service.update_from_loaded(
        image_id=image_id,
        place_id=place_id,
        image_url=None,
        loaded=loaded,
        expected_sha256=image_sha256,
    )


@router.delete(
    "/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERROR_RESPONSES,
)
async def delete_image_index(image_id: str, request: Request) -> Response:
    request.app.state.indexing_service.delete(image_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/rebuild",
    response_model=RebuildResponse,
    responses=ERROR_RESPONSES,
)
async def rebuild_index(request: Request) -> RebuildResponse:
    return request.app.state.indexing_service.rebuild()
