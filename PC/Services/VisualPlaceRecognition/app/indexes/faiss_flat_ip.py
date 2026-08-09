"""Exact cosine-similarity search using FAISS IndexFlatIP."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import faiss
import numpy as np

from app.indexes.base import VectorIndex


class FaissFlatIPIndex(VectorIndex):
    """Normalize vectors internally and perform exact inner-product search."""

    def __init__(self, dimension: int | None = None) -> None:
        if dimension is not None and dimension <= 0:
            raise ValueError("Index dimension must be greater than zero")
        self._index: faiss.IndexFlatIP | None = (
            faiss.IndexFlatIP(dimension) if dimension is not None else None
        )

    @property
    def size(self) -> int:
        return int(self._index.ntotal) if self._index is not None else 0

    @property
    def dimension(self) -> int | None:
        return int(self._index.d) if self._index is not None else None

    @property
    def index_type(self) -> str:
        return "IndexFlatIP"

    def rebuild(self, vectors: np.ndarray) -> None:
        prepared = self._prepare_vectors(vectors, allow_empty=True)
        dimension = int(prepared.shape[1])
        if dimension <= 0:
            if self.dimension is None:
                self._index = None
            else:
                self._index = faiss.IndexFlatIP(self.dimension)
            return
        index = faiss.IndexFlatIP(dimension)
        if prepared.shape[0]:
            index.add(prepared)
        self._index = index

    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        prepared = self._prepare_vectors(query)
        if self._index is None or self.size == 0:
            return (
                np.empty((prepared.shape[0], 0), dtype=np.float32),
                np.empty((prepared.shape[0], 0), dtype=np.int64),
            )
        if prepared.shape[1] != self.dimension:
            raise ValueError(
                f"Query dimension {prepared.shape[1]} does not match index dimension {self.dimension}"
            )
        k = min(top_k, self.size)
        scores, indices = self._index.search(prepared, k)
        return scores.astype(np.float32, copy=False), indices.astype(np.int64, copy=False)

    def save(self, path: Path) -> None:
        if self._index is None:
            raise ValueError("Cannot save an index without a known dimension")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        )
        temp_path = Path(handle.name)
        handle.close()
        try:
            faiss.write_index(self._index, str(temp_path))
            with temp_path.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    def load(self, path: Path) -> None:
        loaded = faiss.read_index(str(path))
        if not isinstance(loaded, faiss.IndexFlatIP):
            raise ValueError(f"Expected IndexFlatIP, got {type(loaded).__name__}")
        if loaded.d <= 0:
            raise ValueError("Loaded index has an invalid dimension")
        if loaded.ntotal < 0:
            raise ValueError("Loaded index has an invalid size")
        if self.dimension is not None and loaded.d != self.dimension:
            raise ValueError(
                f"Loaded dimension {loaded.d} does not match expected {self.dimension}"
            )
        self._index = loaded

    @staticmethod
    def _prepare_vectors(vectors: np.ndarray, *, allow_empty: bool = False) -> np.ndarray:
        result = np.asarray(vectors, dtype=np.float32)
        if result.ndim == 1:
            result = result.reshape(1, -1)
        if result.ndim != 2:
            raise ValueError("Vectors must be a one- or two-dimensional array")
        result = np.ascontiguousarray(result, dtype=np.float32)
        if result.shape[1] == 0:
            if allow_empty and result.shape[0] == 0:
                return result.copy()
            raise ValueError("Vectors must have a non-zero dimension")
        if not np.isfinite(result).all():
            raise ValueError("Vectors contain non-finite values")
        if result.shape[0] == 0:
            if allow_empty:
                return result.copy()
            raise ValueError("No vectors were provided")
        norms = np.linalg.norm(result, axis=1, keepdims=True)
        if np.any(norms <= 0.0):
            raise ValueError("Zero vectors cannot be normalized")
        return np.ascontiguousarray(result / norms, dtype=np.float32)

