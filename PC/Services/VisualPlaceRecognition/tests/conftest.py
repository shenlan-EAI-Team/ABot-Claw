from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import numpy as np
import httpx
import pytest
import pytest_asyncio
from PIL import Image

from app.config import Settings
from app.descriptors.base import GlobalDescriptor
from app.image_io import ImageLoader
from app.main import create_app
from app.services.decision_service import DecisionService
from app.services.indexing_service import IndexingService
from app.services.recognition_service import RecognitionService
from app.storage.database import SQLiteDatabase
from app.storage.repository import VisualIndexRepository


class MockDescriptor(GlobalDescriptor):
    """Small deterministic descriptor derived from mean RGB pixel values."""

    def __init__(self, version: str = "mock_v1") -> None:
        self._version = version
        self._loaded = False
        self.encode_calls = 0
        self.fail = False

    @property
    def backend(self) -> str:
        return "mock"

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimension(self) -> int:
        return 4

    @property
    def device(self) -> str:
        return "cpu"

    @property
    def model_loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        self._loaded = True

    def encode(self, image: Image.Image) -> np.ndarray:
        if not self._loaded:
            raise RuntimeError("mock model is not loaded")
        if self.fail:
            raise RuntimeError("mock inference failed")
        self.encode_calls += 1
        mean = np.asarray(image.convert("RGB"), dtype=np.float32).mean(axis=(0, 1)) / 255.0
        return np.asarray([mean[0], mean[1], mean[2], 0.25], dtype=np.float32)


def make_settings(tmp_path: Path, **updates: object) -> Settings:
    base = Settings.from_env()
    data_dir = tmp_path / "data"
    values: dict[str, object] = {
        "device": "cpu",
        "data_dir": data_dir,
        "index_path": data_dir / "index.faiss",
        "database_path": data_dir / "index.sqlite3",
        "cache_dir": data_dir / "cache",
        "descriptor_backend": "salad",
        "descriptor_version": "mock_v1",
        "local_image_roots": (tmp_path.resolve(),),
        "max_image_bytes": 1024 * 1024,
        "unknown_threshold": 0.60,
        "ambiguous_margin": 0.08,
    }
    values.update(updates)
    result = replace(base, **values)
    result.ensure_directories()
    return result


def image_bytes(color: tuple[int, int, int], *, size: tuple[int, int] = (24, 24)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def settings_factory(tmp_path: Path):
    def factory(**updates: object) -> Settings:
        return make_settings(tmp_path, **updates)

    return factory


@pytest.fixture
def service_stack(settings_factory):
    settings = settings_factory()
    database = SQLiteDatabase(settings.database_path)
    database.initialize()
    repository = VisualIndexRepository(database)
    descriptor = MockDescriptor()
    descriptor.load()
    image_loader = ImageLoader(settings)
    indexing = IndexingService(settings, descriptor, repository, image_loader)
    indexing.initialize()
    decision = DecisionService(settings.unknown_threshold, settings.ambiguous_margin)
    recognition = RecognitionService(settings, descriptor, indexing, decision)
    return {
        "settings": settings,
        "database": database,
        "repository": repository,
        "descriptor": descriptor,
        "image_loader": image_loader,
        "indexing": indexing,
        "recognition": recognition,
    }


@pytest_asyncio.fixture
async def api_context(settings_factory):
    settings = settings_factory()
    descriptor = MockDescriptor()
    application = create_app(settings, descriptor)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, application, descriptor, settings
