"""Abstract vector index contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np


class VectorIndex(ABC):
    @abstractmethod
    def rebuild(self, vectors: np.ndarray) -> None:
        ...

    @abstractmethod
    def search(self, query: np.ndarray, top_k: int) -> tuple[np.ndarray, np.ndarray]:
        ...

    @abstractmethod
    def save(self, path: Path) -> None:
        ...

    @abstractmethod
    def load(self, path: Path) -> None:
        ...

    @property
    @abstractmethod
    def size(self) -> int:
        ...

    @property
    @abstractmethod
    def dimension(self) -> int | None:
        ...

