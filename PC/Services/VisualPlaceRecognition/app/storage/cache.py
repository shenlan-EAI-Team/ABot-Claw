"""Atomic reference-image and descriptor cache managed under configured roots."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

import numpy as np

from app.config import Settings
from app.descriptors.base import GlobalDescriptor, validate_descriptor
from app.image_io import LoadedImage
from app.storage.models import VisualIndexEntry


LOGGER = logging.getLogger(__name__)


class InvalidEmbeddingCache(ValueError):
    """The cached vector cannot be used with the current descriptor contract."""


class VisualCache:
    """Bind cached files to image hashes and the active descriptor identity."""

    def __init__(self, settings: Settings, descriptor: GlobalDescriptor) -> None:
        self.settings = settings
        self.descriptor = descriptor

    def matches_descriptor(self, entry: VisualIndexEntry) -> bool:
        return (
            entry.descriptor_backend == self.descriptor.backend
            and entry.descriptor_version == self.descriptor.version
            and entry.normalized
        )

    def load_embedding(self, entry: VisualIndexEntry) -> np.ndarray:
        if not self.matches_descriptor(entry):
            raise InvalidEmbeddingCache("Descriptor metadata changed")
        path = Path(entry.embedding_cache_path).expanduser().resolve()
        if not path.is_relative_to(self.settings.embedding_cache_dir.resolve()):
            raise InvalidEmbeddingCache("Embedding cache path is outside the cache root")
        try:
            vector = validate_descriptor(np.load(path, allow_pickle=False))
        except Exception as exc:
            raise InvalidEmbeddingCache("Embedding cache cannot be loaded") from exc
        if vector.ndim != 1 or vector.size != entry.descriptor_dimension:
            raise InvalidEmbeddingCache("Embedding cache dimension is invalid")
        return vector

    def write_embedding(
        self,
        image_id: str,
        image_sha256: str,
        vector: np.ndarray,
    ) -> Path:
        vector = validate_descriptor(vector)
        safe_id = hashlib.sha256(image_id.encode("utf-8")).hexdigest()[:24]
        model_key = hashlib.sha256(
            f"{self.descriptor.backend}:{self.descriptor.version}".encode("utf-8")
        ).hexdigest()[:12]
        path = self.settings.embedding_cache_dir / (
            f"{safe_id}-{image_sha256[:16]}-{model_key}-{vector.size}.npy"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                np.save(handle, vector, allow_pickle=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return path

    def write_image(self, loaded: LoadedImage) -> Path:
        extension = {"JPEG": "jpg", "TIFF": "tiff"}.get(
            loaded.image_format,
            loaded.image_format.lower(),
        )
        path = self.settings.image_cache_dir / f"{loaded.sha256}.{extension}"
        if path.exists():
            try:
                if hashlib.sha256(path.read_bytes()).hexdigest() == loaded.sha256:
                    return path
            except OSError as exc:
                LOGGER.debug("Unable to verify existing image cache name=%s: %s", path.name, exc)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                handle.write(loaded.raw_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return path

    def unlink_embedding(self, path: str | Path) -> None:
        self._safe_unlink(Path(path), self.settings.embedding_cache_dir)

    def unlink_image(self, path: str | Path) -> None:
        self._safe_unlink(Path(path), self.settings.image_cache_dir)

    @staticmethod
    def _safe_unlink(path: Path, root: Path) -> None:
        try:
            resolved = path.expanduser().resolve()
            if resolved.is_relative_to(root.resolve()):
                resolved.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Unable to clean derived cache file name=%s", path.name)

