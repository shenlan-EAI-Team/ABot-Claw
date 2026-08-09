"""Descriptor abstraction kept independent from indexing and business metadata."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
from PIL import Image


class GlobalDescriptor(ABC):
    """Turn one RGB image into one unnormalized global descriptor."""

    @property
    @abstractmethod
    def backend(self) -> str:
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        ...

    @property
    @abstractmethod
    def dimension(self) -> int | None:
        ...

    @property
    @abstractmethod
    def device(self) -> str:
        ...

    @property
    @abstractmethod
    def model_loaded(self) -> bool:
        ...

    @abstractmethod
    def load(self) -> None:
        """Load model weights exactly once for this process."""

    @abstractmethod
    def encode(self, image: Image.Image) -> np.ndarray:
        """Return one finite, non-zero, unnormalized float32 vector."""


def validate_descriptor(vector: np.ndarray) -> np.ndarray:
    """Validate the common descriptor contract without normalizing the vector."""
    result = np.asarray(vector, dtype=np.float32).reshape(-1)
    if result.size == 0:
        raise ValueError("Descriptor is empty")
    if not np.isfinite(result).all():
        raise ValueError("Descriptor contains non-finite values")
    if not np.any(result):
        raise ValueError("Descriptor is a zero vector")
    return np.ascontiguousarray(result, dtype=np.float32)

