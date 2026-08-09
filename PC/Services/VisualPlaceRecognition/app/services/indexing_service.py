"""Transactional reference-image indexing and durable FAISS snapshot management."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from app.config import Settings
from app.descriptors.base import GlobalDescriptor, validate_descriptor
from app.errors import VPRServiceError, model_not_ready
from app.image_io import ImageLoader, LoadedImage
from app.indexes.faiss_flat_ip import FaissFlatIPIndex
from app.schemas import ImageIndexResponse, RebuildResponse
from app.storage.cache import InvalidEmbeddingCache, VisualCache
from app.storage.models import VisualIndexEntry
from app.storage.repository import VisualIndexRepository


LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class IndexSnapshot:
    """An immutable pairing of one FAISS index and its positional ID mapping."""

    index: FaissFlatIPIndex
    entries: tuple[VisualIndexEntry, ...]
    version: int
    last_rebuild_at: str | None
    loaded: bool


class IndexingService:
    """Own reference entries, embedding cache, durable index, and atomic snapshots."""

    def __init__(
        self,
        settings: Settings,
        descriptor: GlobalDescriptor,
        repository: VisualIndexRepository,
        image_loader: ImageLoader,
    ) -> None:
        self.settings = settings
        self.descriptor = descriptor
        self.repository = repository
        self.image_loader = image_loader
        self.cache = VisualCache(settings, descriptor)
        self._mutation_lock = threading.RLock()
        self._snapshot_lock = threading.Lock()
        self._snapshot = IndexSnapshot(FaissFlatIPIndex(), (), 0, None, False)
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def get_snapshot(self) -> IndexSnapshot:
        """Return a stable index/mapping pair; callers may search it without a write lock."""
        with self._snapshot_lock:
            return self._snapshot

    def initialize(self) -> None:
        """Restore a valid persisted index or safely rebuild it from active metadata."""
        with self._mutation_lock:
            entries = self.repository.list_active()
            version = self._read_index_version()
            last_rebuild_at = self.repository.get_state("last_rebuild_at")
            if not entries:
                empty = FaissFlatIPIndex()
                if self.settings.index_path.exists():
                    try:
                        empty.load(self.settings.index_path)
                        if empty.size != 0:
                            raise ValueError("Persisted index is non-empty but database is empty")
                    except Exception:
                        LOGGER.exception("Ignoring inconsistent empty persisted index")
                        empty = FaissFlatIPIndex()
                self._publish(empty, (), version, last_rebuild_at, loaded=True)
                self._last_error = None
                return

            try:
                self._validate_entry_set(entries)
                index = FaissFlatIPIndex(entries[0].descriptor_dimension)
                index.load(self.settings.index_path)
                if index.size != len(entries):
                    raise ValueError(
                        f"Index size {index.size} does not match metadata size {len(entries)}"
                    )
                self._publish(index, tuple(entries), version, last_rebuild_at, loaded=True)
                self._last_error = None
                LOGGER.info("Restored FAISS index with %d entries", len(entries))
            except Exception:
                LOGGER.exception("Persisted index could not be restored; rebuilding")
                self.rebuild()

    def create_from_source(
        self,
        *,
        place_id: str,
        image_id: str,
        image_url: str,
        expected_sha256: str | None,
    ) -> ImageIndexResponse:
        loaded = self.image_loader.load_source(image_url)
        return self.create_from_loaded(
            place_id=place_id,
            image_id=image_id,
            image_url=image_url,
            loaded=loaded,
            expected_sha256=expected_sha256,
        )

    def create_from_loaded(
        self,
        *,
        place_id: str,
        image_id: str,
        image_url: str | None,
        loaded: LoadedImage,
        expected_sha256: str | None,
    ) -> ImageIndexResponse:
        self._ensure_model_ready()
        self._verify_hash(loaded.sha256, expected_sha256)
        with self._mutation_lock:
            existing = self.repository.get_by_image_id(image_id)
            if existing is not None:
                if existing.place_id != place_id:
                    raise self._image_id_conflict(image_id)
                if (
                    existing.image_sha256 == loaded.sha256
                    and existing.descriptor_backend == self.descriptor.backend
                    and existing.descriptor_version == self.descriptor.version
                ):
                    return self._response(existing, created=False)
                raise self._image_id_conflict(image_id)

            vector = self._encode(loaded)
            image_cache_path = self.cache.write_image(loaded)
            embedding_cache_path = self.cache.write_embedding(image_id, loaded.sha256, vector)
            now = self._utc_now()
            source = image_url or f"uploaded://{loaded.sha256}"
            entry = VisualIndexEntry(
                id=None,
                place_id=place_id,
                image_id=image_id,
                image_url=source,
                image_sha256=loaded.sha256,
                image_cache_path=str(image_cache_path),
                embedding_cache_path=str(embedding_cache_path),
                descriptor_backend=self.descriptor.backend,
                descriptor_version=self.descriptor.version,
                descriptor_dimension=int(vector.size),
                normalized=True,
                active=True,
                created_at=now,
                updated_at=now,
                indexed_at=now,
                last_error=None,
            )

            try:
                def mutation(connection: sqlite3.Connection) -> VisualIndexEntry:
                    return self.repository.create(entry, connection)

                created_entry, snapshot = self._commit_mutation(mutation)
            except sqlite3.IntegrityError as exc:
                self.cache.unlink_embedding(embedding_cache_path)
                raise self._image_id_conflict(image_id) from exc
            except Exception:
                self.cache.unlink_embedding(embedding_cache_path)
                raise
            return self._response(
                self._find_entry(snapshot.entries, created_entry.image_id),
                created=True,
            )

    def update_from_source(
        self,
        *,
        image_id: str,
        place_id: str | None,
        image_url: str | None,
        expected_sha256: str | None,
    ) -> ImageIndexResponse:
        loaded = self.image_loader.load_source(image_url) if image_url is not None else None
        return self.update_from_loaded(
            image_id=image_id,
            place_id=place_id,
            image_url=image_url,
            loaded=loaded,
            expected_sha256=expected_sha256,
        )

    def update_from_loaded(
        self,
        *,
        image_id: str,
        place_id: str | None,
        image_url: str | None,
        loaded: LoadedImage | None,
        expected_sha256: str | None,
    ) -> ImageIndexResponse:
        self._ensure_model_ready()
        if loaded is not None:
            self._verify_hash(loaded.sha256, expected_sha256)
        with self._mutation_lock:
            existing = self.repository.get_by_image_id(image_id)
            if existing is None:
                raise VPRServiceError(404, "ENTRY_NOT_FOUND", "Visual index entry was not found")

            target_place_id = place_id or existing.place_id
            source = (
                image_url
                if image_url is not None
                else (f"uploaded://{loaded.sha256}" if loaded is not None else existing.image_url)
            )
            new_embedding_path: Path | None = None
            if loaded is None and self.cache.matches_descriptor(existing):
                updated = replace(
                    existing,
                    place_id=target_place_id,
                    updated_at=self._utc_now(),
                    indexed_at=self._utc_now(),
                    last_error=None,
                )
            else:
                if loaded is None:
                    loaded = self._load_entry_image(existing)
                vector = self._encode(loaded)
                image_cache_path = self.cache.write_image(loaded)
                new_embedding_path = self.cache.write_embedding(image_id, loaded.sha256, vector)
                now = self._utc_now()
                updated = replace(
                    existing,
                    place_id=target_place_id,
                    image_url=source,
                    image_sha256=loaded.sha256,
                    image_cache_path=str(image_cache_path),
                    embedding_cache_path=str(new_embedding_path),
                    descriptor_backend=self.descriptor.backend,
                    descriptor_version=self.descriptor.version,
                    descriptor_dimension=int(vector.size),
                    normalized=True,
                    active=True,
                    updated_at=now,
                    indexed_at=now,
                    last_error=None,
                )

            def mutation(connection: sqlite3.Connection) -> VisualIndexEntry:
                result = self.repository.update(
                    image_id,
                    self._entry_updates(updated),
                    connection,
                )
                if result is None:
                    raise VPRServiceError(404, "ENTRY_NOT_FOUND", "Visual index entry was not found")
                return result

            try:
                updated_entry, snapshot = self._commit_mutation(mutation)
            except Exception:
                if new_embedding_path is not None and str(new_embedding_path) != existing.embedding_cache_path:
                    self.cache.unlink_embedding(new_embedding_path)
                raise
            if existing.embedding_cache_path != updated_entry.embedding_cache_path:
                self.cache.unlink_embedding(existing.embedding_cache_path)
            return self._response(
                self._find_entry(snapshot.entries, updated_entry.image_id),
                created=False,
            )

    def delete(self, image_id: str) -> None:
        with self._mutation_lock:
            existing = self.repository.get_by_image_id(image_id)
            if existing is None:
                raise VPRServiceError(404, "ENTRY_NOT_FOUND", "Visual index entry was not found")

            def mutation(connection: sqlite3.Connection) -> None:
                if not self.repository.delete(image_id, connection):
                    raise VPRServiceError(404, "ENTRY_NOT_FOUND", "Visual index entry was not found")
                return None

            self._commit_mutation(mutation)
            self.cache.unlink_embedding(existing.embedding_cache_path)
            remaining = self.repository.list_active()
            if not any(item.image_cache_path == existing.image_cache_path for item in remaining):
                self.cache.unlink_image(existing.image_cache_path)

    def rebuild(self) -> RebuildResponse:
        """Regenerate invalid embeddings and atomically replace the complete index."""
        self._ensure_model_ready()
        started = time.perf_counter()
        with self._mutation_lock:
            previous_size = self.get_snapshot().index.size
            entries = self.repository.list_active()
            refreshed: dict[str, VisualIndexEntry] = {}
            failed: list[str] = []
            for entry in entries:
                try:
                    self.cache.load_embedding(entry)
                    refreshed[entry.image_id] = entry
                except InvalidEmbeddingCache:
                    try:
                        loaded = self._load_entry_image(entry)
                        vector = self._encode(loaded)
                        embedding_path = self.cache.write_embedding(
                            entry.image_id,
                            loaded.sha256,
                            vector,
                        )
                        now = self._utc_now()
                        refreshed[entry.image_id] = replace(
                            entry,
                            image_sha256=loaded.sha256,
                            image_cache_path=str(self.cache.write_image(loaded)),
                            embedding_cache_path=str(embedding_path),
                            descriptor_backend=self.descriptor.backend,
                            descriptor_version=self.descriptor.version,
                            descriptor_dimension=int(vector.size),
                            normalized=True,
                            updated_at=now,
                            indexed_at=now,
                            last_error=None,
                        )
                    except Exception:
                        LOGGER.exception("Unable to rebuild image_id=%s", entry.image_id)
                        failed.append(entry.image_id)
            if failed:
                for image_id in failed:
                    self.repository.set_last_error(image_id, "Reference image rebuild failed")
                self._last_error = f"Failed to rebuild {len(failed)} entries"
                raise VPRServiceError(
                    500,
                    "INDEX_REBUILD_FAILED",
                    "Unable to rebuild all active visual index entries",
                    {"failed_entries": failed},
                )

            def mutation(connection: sqlite3.Connection) -> None:
                for entry in refreshed.values():
                    self.repository.update(
                        entry.image_id,
                        self._entry_updates(entry),
                        connection,
                    )
                return None

            _, snapshot = self._commit_mutation(mutation)
            for old_entry in entries:
                new_entry = refreshed[old_entry.image_id]
                if old_entry.embedding_cache_path != new_entry.embedding_cache_path:
                    self.cache.unlink_embedding(old_entry.embedding_cache_path)
            duration_ms = (time.perf_counter() - started) * 1000.0
            return RebuildResponse(
                previous_index_size=previous_size,
                index_size=snapshot.index.size,
                duration_ms=duration_ms,
                index_version=snapshot.version,
                failed_entries=[],
            )

    def _commit_mutation(
        self,
        mutation: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, IndexSnapshot]:
        old_snapshot = self.get_snapshot()
        disk_changed = False
        stage_path: Path | None = None
        try:
            with self.repository.database.transaction() as connection:
                result = mutation(connection)
                entries = self.repository.list_active(connection)
                index = self._build_index(entries, old_snapshot.index.dimension)
                if index.size != len(entries):
                    raise ValueError("FAISS size does not match active metadata count")
                version = int(self.repository.get_state("index_version", connection) or "0") + 1
                rebuilt_at = self._utc_now()
                self.repository.set_state("index_version", str(version), connection)
                self.repository.set_state("last_rebuild_at", rebuilt_at, connection)
                stage_path = self._stage_index(index)
                disk_changed = self._install_staged_index(stage_path, index)
                stage_path = None
            snapshot = self._publish(index, tuple(entries), version, rebuilt_at, loaded=True)
            self._last_error = None
            return result, snapshot
        except VPRServiceError:
            if disk_changed:
                self._restore_disk_snapshot(old_snapshot)
            raise
        except Exception as exc:
            if disk_changed:
                self._restore_disk_snapshot(old_snapshot)
            self._last_error = "Index rebuild failed"
            LOGGER.exception("Index mutation failed; previous snapshot retained")
            raise VPRServiceError(
                500,
                "INDEX_REBUILD_FAILED",
                "Unable to atomically rebuild the visual index",
            ) from exc
        finally:
            if stage_path is not None:
                stage_path.unlink(missing_ok=True)

    def _build_index(
        self,
        entries: list[VisualIndexEntry],
        fallback_dimension: int | None,
    ) -> FaissFlatIPIndex:
        if not entries:
            return FaissFlatIPIndex(fallback_dimension or self.descriptor.dimension)
        vectors = [self.cache.load_embedding(entry) for entry in entries]
        dimensions = {int(vector.size) for vector in vectors}
        if len(dimensions) != 1:
            raise InvalidEmbeddingCache("Active embeddings have inconsistent dimensions")
        index = FaissFlatIPIndex()
        index.rebuild(np.vstack(vectors))
        return index

    def _load_entry_image(self, entry: VisualIndexEntry) -> LoadedImage:
        try:
            loaded = self.image_loader.load_cached(entry.image_cache_path)
            if loaded.sha256 == entry.image_sha256:
                return loaded
            LOGGER.warning("Cached image hash mismatch for image_id=%s", entry.image_id)
        except VPRServiceError:
            LOGGER.warning("Cached image unavailable for image_id=%s", entry.image_id)
        if entry.image_url.startswith("uploaded://"):
            raise VPRServiceError(
                400,
                "IMAGE_DOWNLOAD_FAILED",
                "Uploaded reference image cache is unavailable",
            )
        loaded = self.image_loader.load_source(entry.image_url)
        self._verify_hash(loaded.sha256, entry.image_sha256)
        return loaded

    def _encode(self, loaded: LoadedImage) -> np.ndarray:
        try:
            return validate_descriptor(self.descriptor.encode(loaded.image))
        except VPRServiceError:
            raise
        except Exception as exc:
            LOGGER.exception("Descriptor inference failed")
            raise VPRServiceError(
                500,
                "MODEL_INFERENCE_FAILED",
                "Unable to extract image descriptor",
            ) from exc

    def _stage_index(self, index: FaissFlatIPIndex) -> Path | None:
        if index.dimension is None:
            return None
        stage = self.settings.index_path.with_name(
            f".{self.settings.index_path.name}.{uuid.uuid4().hex}.stage"
        )
        index.save(stage)
        return stage

    def _install_staged_index(
        self,
        stage_path: Path | None,
        index: FaissFlatIPIndex,
    ) -> bool:
        if stage_path is None or index.dimension is None:
            if self.settings.index_path.exists():
                self.settings.index_path.unlink()
                return True
            return False
        self.settings.index_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage_path, self.settings.index_path)
        return True

    def _restore_disk_snapshot(self, snapshot: IndexSnapshot) -> None:
        try:
            if snapshot.index.dimension is None:
                self.settings.index_path.unlink(missing_ok=True)
            else:
                snapshot.index.save(self.settings.index_path)
        except Exception:
            LOGGER.exception("Unable to restore previous persisted FAISS index")

    def _publish(
        self,
        index: FaissFlatIPIndex,
        entries: tuple[VisualIndexEntry, ...],
        version: int,
        rebuilt_at: str | None,
        *,
        loaded: bool,
    ) -> IndexSnapshot:
        snapshot = IndexSnapshot(index, entries, version, rebuilt_at, loaded)
        with self._snapshot_lock:
            self._snapshot = snapshot
        return snapshot

    def _validate_entry_set(self, entries: list[VisualIndexEntry]) -> None:
        dimensions = {entry.descriptor_dimension for entry in entries}
        if len(dimensions) != 1 or next(iter(dimensions)) <= 0:
            raise ValueError("Active metadata has inconsistent descriptor dimensions")
        for entry in entries:
            if not self.cache.matches_descriptor(entry):
                raise ValueError("Active metadata uses a different descriptor version")

    def _read_index_version(self) -> int:
        try:
            return int(self.repository.get_state("index_version") or "0")
        except ValueError:
            LOGGER.warning("Invalid persisted index_version; resetting to zero")
            return 0

    def _ensure_model_ready(self) -> None:
        if not self.descriptor.model_loaded:
            raise model_not_ready()

    @staticmethod
    def _verify_hash(actual: str, expected: str | None) -> None:
        if expected is not None and actual.lower() != expected.lower():
            raise VPRServiceError(
                409,
                "IMAGE_HASH_MISMATCH",
                "Downloaded image SHA-256 does not match the supplied value",
            )

    @staticmethod
    def _image_id_conflict(image_id: str) -> VPRServiceError:
        return VPRServiceError(
            409,
            "IMAGE_ID_CONFLICT",
            "image_id already exists with different content or metadata; use PUT to update",
            {"image_id": image_id},
        )

    @staticmethod
    def _entry_updates(entry: VisualIndexEntry) -> dict[str, Any]:
        return {
            "place_id": entry.place_id,
            "image_url": entry.image_url,
            "image_sha256": entry.image_sha256,
            "image_cache_path": entry.image_cache_path,
            "embedding_cache_path": entry.embedding_cache_path,
            "descriptor_backend": entry.descriptor_backend,
            "descriptor_version": entry.descriptor_version,
            "descriptor_dimension": entry.descriptor_dimension,
            "normalized": entry.normalized,
            "active": entry.active,
            "updated_at": entry.updated_at,
            "indexed_at": entry.indexed_at,
            "last_error": entry.last_error,
        }

    @staticmethod
    def _find_entry(entries: tuple[VisualIndexEntry, ...], image_id: str) -> VisualIndexEntry:
        for entry in entries:
            if entry.image_id == image_id:
                return entry
        raise RuntimeError("Committed entry is missing from the published mapping")

    def _response(self, entry: VisualIndexEntry, *, created: bool) -> ImageIndexResponse:
        snapshot = self.get_snapshot()
        indexed_at = datetime.fromisoformat(entry.indexed_at or entry.updated_at)
        return ImageIndexResponse(
            created=created,
            place_id=entry.place_id,
            image_id=entry.image_id,
            image_sha256=entry.image_sha256,
            descriptor_backend=entry.descriptor_backend,
            descriptor_version=entry.descriptor_version,
            descriptor_dimension=entry.descriptor_dimension,
            normalized=entry.normalized,
            index_size=snapshot.index.size,
            indexed_at=indexed_at,
        )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()
