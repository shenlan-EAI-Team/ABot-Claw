"""Pydantic request and response contracts for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Decision = Literal["matched", "ambiguous", "unknown", "empty_index"]


class ImageIndexCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    place_id: str = Field(min_length=1, max_length=255)
    image_id: str = Field(min_length=1, max_length=255)
    image_url: str = Field(min_length=1, max_length=4096)
    image_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("image_sha256")
    @classmethod
    def normalize_hash(cls, value: str | None) -> str | None:
        return value.lower() if value else None


class ImageIndexUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    place_id: str | None = Field(default=None, min_length=1, max_length=255)
    image_url: str | None = Field(default=None, min_length=1, max_length=4096)
    image_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("image_sha256")
    @classmethod
    def normalize_hash(cls, value: str | None) -> str | None:
        return value.lower() if value else None

    @model_validator(mode="after")
    def require_change(self) -> "ImageIndexUpdateRequest":
        if self.place_id is None and self.image_url is None:
            raise ValueError("At least one of place_id or image_url must be provided")
        if self.image_sha256 is not None and self.image_url is None:
            raise ValueError("image_sha256 requires image_url")
        return self


class ImageIndexResponse(BaseModel):
    status: Literal["indexed"] = "indexed"
    created: bool
    place_id: str
    image_id: str
    image_sha256: str
    descriptor_backend: str
    descriptor_version: str
    descriptor_dimension: int
    normalized: bool
    index_size: int
    indexed_at: datetime


class SearchCandidate(BaseModel):
    rank: int
    place_id: str
    image_id: str
    score: float


class SearchResponse(BaseModel):
    decision: Decision
    candidates: list[SearchCandidate]
    top1_score: float | None
    top2_score: float | None
    margin: float | None
    unknown_threshold: float
    ambiguous_margin: float


class VerifyResponse(BaseModel):
    verified: bool
    decision: Decision
    target_place_id: str
    target_rank: int | None
    target_score: float | None
    top1_place_id: str | None
    top1_score: float | None
    top2_place_id: str | None
    top2_score: float | None
    margin: float | None
    reasons: list[str]


class IndexStatusResponse(BaseModel):
    ready: bool
    model_loaded: bool
    descriptor_backend: str
    descriptor_version: str
    descriptor_dimension: int | None
    device: str
    index_type: str
    index_size: int
    index_loaded: bool
    database_entries: int
    index_version: int
    unknown_threshold: float
    ambiguous_margin: float
    last_rebuild_at: datetime | None
    last_error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "VisualPlaceRecognition"


class RebuildResponse(BaseModel):
    status: Literal["rebuilt"] = "rebuilt"
    previous_index_size: int
    index_size: int
    duration_ms: float
    index_version: int
    failed_entries: list[str] = Field(default_factory=list)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody

