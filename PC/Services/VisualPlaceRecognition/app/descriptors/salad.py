"""Official Torch Hub SALAD global image descriptor."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.descriptors.base import GlobalDescriptor, validate_descriptor
from app.errors import VPRServiceError, model_not_ready


LOGGER = logging.getLogger(__name__)


class SaladDescriptor(GlobalDescriptor):
    """Load DINOv2-SALAD once and expose its raw descriptor dimension dynamically."""

    _cache_lock = threading.Lock()
    _model_cache: dict[tuple[str, str, str], tuple[Any, Any]] = {}

    def __init__(
        self,
        *,
        version: str,
        requested_device: str = "auto",
        repo: str = "serizba/salad",
        model_name: str = "dinov2_salad",
        image_size: tuple[int, int] = (322, 322),
    ) -> None:
        self._version = version
        self._requested_device = requested_device
        self._repo = repo
        self._model_name = model_name
        self._image_size = image_size
        self._device = requested_device
        self._model: Any | None = None
        self._preprocess: Any | None = None
        self._dimension: int | None = None
        self._load_error: str | None = None
        self._inference_lock = threading.Lock()

    @property
    def backend(self) -> str:
        return "salad"

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimension(self) -> int | None:
        return self._dimension

    @property
    def device(self) -> str:
        return self._device

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def load(self) -> None:
        """Load the official pretrained model and evaluation preprocessing."""
        if self._model is not None:
            return
        try:
            import torch
            from torchvision import transforms

            self._device = self._resolve_device(torch, self._requested_device)
            cache_key = (self._repo, self._model_name, self._device)
            with self._cache_lock:
                cached = self._model_cache.get(cache_key)
                if cached is None:
                    LOGGER.info(
                        "Loading SALAD model repo=%s model=%s device=%s",
                        self._repo,
                        self._model_name,
                        self._device,
                    )
                    hub_kwargs: dict[str, Any] = {
                        "pretrained": True,
                        "trust_repo": True,
                    }
                    if Path(self._repo).expanduser().exists():
                        hub_kwargs["source"] = "local"
                    model = torch.hub.load(self._repo, self._model_name, **hub_kwargs)
                    model.eval()
                    model.to(self._device)
                    preprocess = transforms.Compose(
                        [
                            transforms.Resize(
                                self._image_size,
                                interpolation=transforms.InterpolationMode.BILINEAR,
                            ),
                            transforms.ToTensor(),
                            transforms.Normalize(
                                mean=[0.485, 0.456, 0.406],
                                std=[0.229, 0.224, 0.225],
                            ),
                        ]
                    )
                    cached = (model, preprocess)
                    self._model_cache[cache_key] = cached
            self._model, self._preprocess = cached
            self._load_error = None
            LOGGER.info("SALAD model loaded on %s", self._device)
        except Exception as exc:
            self._model = None
            self._preprocess = None
            self._load_error = str(exc)
            LOGGER.exception("Unable to load SALAD model")
            raise model_not_ready("Unable to load the SALAD model") from exc

    def encode(self, image: Image.Image) -> np.ndarray:
        """Run a single-image inference and return the raw float32 descriptor."""
        if self._model is None or self._preprocess is None:
            raise model_not_ready()
        try:
            import torch

            tensor = self._preprocess(image.convert("RGB")).unsqueeze(0).to(self._device)
            with self._inference_lock, torch.inference_mode():
                output = self._model(tensor)
            output_tensor = self._select_tensor(output, torch)
            vector = validate_descriptor(
                output_tensor.detach().to(device="cpu", dtype=torch.float32).numpy()
            )
            if self._dimension is None:
                self._dimension = int(vector.size)
                LOGGER.info("Detected SALAD descriptor dimension=%d", self._dimension)
            elif vector.size != self._dimension:
                raise ValueError(
                    f"Descriptor dimension changed from {self._dimension} to {vector.size}"
                )
            return vector
        except VPRServiceError:
            raise
        except Exception as exc:
            LOGGER.exception("SALAD inference failed")
            raise VPRServiceError(
                500,
                "MODEL_INFERENCE_FAILED",
                "Unable to extract image descriptor",
            ) from exc

    @staticmethod
    def _resolve_device(torch: Any, requested: str) -> str:
        value = requested.strip().lower()
        if value == "auto":
            return "cuda:0" if torch.cuda.is_available() else "cpu"
        if value == "cpu":
            return "cpu"
        if value == "cuda":
            value = "cuda:0"
        if value.startswith("cuda:"):
            if not torch.cuda.is_available():
                raise RuntimeError(f"Requested device {value} but CUDA is unavailable")
            index = int(value.split(":", 1)[1])
            if index < 0 or index >= torch.cuda.device_count():
                raise RuntimeError(f"Requested CUDA device does not exist: {value}")
            return value
        raise RuntimeError(f"Unsupported device: {requested}")

    @staticmethod
    def _select_tensor(output: Any, torch: Any) -> Any:
        if torch.is_tensor(output):
            return output
        if isinstance(output, Mapping):
            for key in ("global_descriptor", "descriptor", "descriptors", "embedding"):
                candidate = output.get(key)
                if torch.is_tensor(candidate):
                    return candidate
        if isinstance(output, Sequence) and not isinstance(output, (str, bytes)):
            for candidate in output:
                if torch.is_tensor(candidate):
                    return candidate
        raise TypeError("SALAD model output does not contain a tensor")
