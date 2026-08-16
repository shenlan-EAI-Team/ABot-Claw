"""Robot-side HTTP client for the PC-hosted VLAC critic service.

Image arguments may be an already encoded base64 string or an RGB ``uint8``
NumPy array.  Array encoding deliberately lives here so callers cannot swap
RGB/BGR order or reverse the before/after critic pair.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional

import cv2
import numpy as np
import requests

try:
    from .config import get_config
except ImportError:
    from config import get_config


class VLACError(RuntimeError):
    """A VLAC request or response could not be used."""


class VLACSDK:
    """G1 Robot -> VLAC HTTP client (the service normally listens on 8014)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> None:
        vlac_cfg = (get_config() or {}).get("vlac") or {}
        configured_url = base_url or os.environ.get("VLAC_URL") or vlac_cfg.get("url")
        if configured_url and "${" not in str(configured_url):
            self._base_url: Optional[str] = str(configured_url).rstrip("/")
        else:
            self._base_url = None

        timeout_value = request_timeout
        if timeout_value is None:
            timeout_value = os.environ.get(
                "VLAC_REQUEST_TIMEOUT",
                vlac_cfg.get("request_timeout", 120.0),
            )
        try:
            self._timeout = float(timeout_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("VLAC request_timeout must be a number") from exc
        if self._timeout <= 0:
            raise ValueError("VLAC request_timeout must be positive")

    def _url(self, path: str) -> str:
        if not self._base_url:
            raise VLACError(
                "VLAC URL is not configured; set VLAC_URL to the PC service, "
                "for example http://<PC_IP>:8014"
            )
        return f"{self._base_url}{path}"

    @staticmethod
    def _encode_image(image: Any, *, field_name: str) -> str:
        """Return a JPEG base64 string, treating NumPy input as RGB."""
        if isinstance(image, str):
            value = image.strip()
            if not value:
                raise VLACError(f"{field_name} must not be an empty string")
            return value

        if not isinstance(image, np.ndarray):
            raise VLACError(
                f"{field_name} must be a base64 string or RGB numpy.ndarray"
            )
        if image.ndim != 3 or image.shape[2] != 3:
            raise VLACError(f"{field_name} must have shape (H, W, 3)")
        if image.dtype != np.uint8:
            raise VLACError(f"{field_name} must have dtype uint8")

        # OpenCV's JPEG encoder consumes BGR; D435i get_frame() returns RGB.
        bgr = cv2.cvtColor(np.ascontiguousarray(image), cv2.COLOR_RGB2BGR)
        ok, encoded = cv2.imencode(".jpg", bgr)
        if not ok:
            raise VLACError(f"failed to JPEG encode {field_name}")
        return base64.b64encode(encoded.tobytes()).decode("ascii")

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = self._url(path)
        try:
            response = requests.request(
                method,
                url,
                timeout=self._timeout,
                **kwargs,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise VLACError(f"VLAC request timed out after {self._timeout:g}s: {url}") from exc
        except requests.RequestException as exc:
            raise VLACError(f"VLAC request failed: {url}: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise VLACError(f"VLAC returned invalid JSON: {url}") from exc
        if not isinstance(data, dict):
            raise VLACError(f"VLAC returned a non-object JSON response: {url}")
        return data

    def health(self) -> Dict[str, Any]:
        """Check service health without raising for service/network failures."""
        try:
            return self._request_json("GET", "/health")
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
                "url": self._base_url,
            }

    def evaluate_progress(
        self,
        before_image: Any,
        after_image: Any,
        task_description: str,
        *,
        batch_num: int = 1,
        rich: bool = False,
    ) -> Dict[str, Any]:
        """Evaluate pair-wise progress with Before as the reference image."""
        if not isinstance(task_description, str) or not task_description.strip():
            raise VLACError("task_description must be a non-empty string")
        if not isinstance(batch_num, int) or batch_num <= 0:
            raise VLACError("batch_num must be a positive integer")

        payload = {
            # Pair order is part of the VLAC contract. Do not reverse these.
            "reference_image": self._encode_image(
                before_image, field_name="before_image"
            ),
            "image": self._encode_image(after_image, field_name="after_image"),
            "task_description": task_description.strip(),
            "batch_num": batch_num,
            "rich": bool(rich),
        }
        result = self._request_json("POST", "/critic", json=payload)
        critic_list = result.get("critic_list")
        if not isinstance(critic_list, list) or not critic_list:
            raise VLACError("VLAC /critic response has an empty or invalid critic_list")
        try:
            float(critic_list[0])
        except (TypeError, ValueError) as exc:
            raise VLACError("VLAC /critic critic_list[0] is not numeric") from exc
        value_list = result.get("value_list")
        if value_list is not None and not isinstance(value_list, list):
            raise VLACError("VLAC /critic value_list is invalid")
        latency_ms = result.get("latency_ms")
        if latency_ms is not None and not isinstance(latency_ms, (int, float)):
            raise VLACError("VLAC /critic latency_ms is invalid")
        return result

    def verify_navigation(
        self,
        current_image: Any,
        reference_image: Any,
        done_threshold: float = 0.8,
        rich: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "current_image": self._encode_image(
                current_image, field_name="current_image"
            ),
            "reference_image": self._encode_image(
                reference_image, field_name="reference_image"
            ),
            "done_threshold": done_threshold,
            "rich": rich,
        }
        return self._request_json("POST", "/navigation/verify", json=payload)

    def verify_grasp(
        self,
        before_image: Any,
        after_image: Any,
        target_label: str,
        rich: bool = False,
    ) -> Dict[str, Any]:
        payload = {
            "before_image": self._encode_image(
                before_image, field_name="before_image"
            ),
            "after_image": self._encode_image(
                after_image, field_name="after_image"
            ),
            "target_label": target_label,
            "rich": rich,
        }
        return self._request_json("POST", "/grasp/verify", json=payload)

    def verify_holding(
        self,
        after_image: Any,
        target_label: str,
        rich: bool = False,
    ) -> Dict[str, Any]:
        payload = {
            "after_image": self._encode_image(
                after_image, field_name="after_image"
            ),
            "target_label": target_label,
            "rich": rich,
        }
        return self._request_json("POST", "/grasp/holding", json=payload)

    def close(self) -> None:
        """Kept for interface compatibility; requests are not session-backed."""
