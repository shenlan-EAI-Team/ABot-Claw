"""Service-level exceptions and stable public error codes."""

from __future__ import annotations

from typing import Any


class VPRServiceError(Exception):
    """An expected failure safe to expose through the public error envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def model_not_ready(message: str = "The visual descriptor model is not ready") -> VPRServiceError:
    return VPRServiceError(503, "MODEL_NOT_READY", message)

