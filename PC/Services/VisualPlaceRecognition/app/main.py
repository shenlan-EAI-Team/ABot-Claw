"""FastAPI application factory and process lifecycle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.index_images import router as index_images_router
from app.api.search import router as search_router
from app.api.status import router as status_router
from app.api.verify import router as verify_router
from app.config import Settings, settings as default_settings
from app.descriptors.base import GlobalDescriptor
from app.descriptors.salad import SaladDescriptor
from app.errors import VPRServiceError
from app.image_io import ImageLoader
from app.services.decision_service import DecisionService
from app.services.indexing_service import IndexingService
from app.services.recognition_service import RecognitionService
from app.storage.database import SQLiteDatabase
from app.storage.repository import VisualIndexRepository


LOGGER = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    descriptor: GlobalDescriptor | None = None,
) -> FastAPI:
    """Create an injectable application; tests pass a deterministic descriptor."""
    runtime_settings = settings or default_settings
    runtime_settings.ensure_directories()
    runtime_descriptor = descriptor or SaladDescriptor(
        version=runtime_settings.descriptor_version,
        requested_device=runtime_settings.device,
        repo=runtime_settings.salad_repo,
        model_name=runtime_settings.salad_model,
        image_size=(
            runtime_settings.salad_image_height,
            runtime_settings.salad_image_width,
        ),
    )
    database = SQLiteDatabase(runtime_settings.database_path)
    repository = VisualIndexRepository(database)
    image_loader = ImageLoader(runtime_settings)
    indexing_service = IndexingService(
        runtime_settings,
        runtime_descriptor,
        repository,
        image_loader,
    )
    decision_service = DecisionService(
        runtime_settings.unknown_threshold,
        runtime_settings.ambiguous_margin,
    )
    recognition_service = RecognitionService(
        runtime_settings,
        runtime_descriptor,
        indexing_service,
        decision_service,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        try:
            runtime_descriptor.load()
        except Exception:
            LOGGER.exception("Descriptor startup failed; liveness remains available")
        try:
            indexing_service.initialize()
        except Exception:
            LOGGER.exception("Index startup recovery failed; readiness is false")
        yield

    application = FastAPI(
        title="Visual Place Recognition",
        version=runtime_settings.service_version,
        description="SALAD global descriptors with exact cosine search via FAISS IndexFlatIP.",
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.descriptor = runtime_descriptor
    application.state.database = database
    application.state.repository = repository
    application.state.image_loader = image_loader
    application.state.indexing_service = indexing_service
    application.state.decision_service = decision_service
    application.state.recognition_service = recognition_service

    @application.exception_handler(VPRServiceError)
    async def service_error_handler(request: Request, exc: VPRServiceError) -> JSONResponse:
        if exc.status_code >= 500:
            LOGGER.error("VPR request failed code=%s path=%s", exc.code, request.url.path)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request validation failed",
                    "details": {"errors": jsonable_encoder(errors)},
                }
            },
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled VPR error path=%s", request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Internal visual recognition service error",
                    "details": {},
                }
            },
        )

    application.include_router(status_router)
    application.include_router(index_images_router)
    application.include_router(search_router)
    application.include_router(verify_router)
    return application


logging.basicConfig(
    level=getattr(logging, default_settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
app = create_app()

