from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Pose(BaseModel):
    x: float
    y: float
    z: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    qx: Optional[float] = None
    qy: Optional[float] = None
    qz: Optional[float] = None
    qw: Optional[float] = None
    frame_id: str = "map"


class NavigationTarget(BaseModel):
    pose: Pose
    confidence: float = 1.0


class ObjectMemoryUpsertRequest(BaseModel):
    object_name: str
    object_id: Optional[str] = None
    robot_id: str
    robot_type: str
    robot_pose: Pose
    object_pose: Pose
    source: str = "yolo"
    bbox_xyxy: Optional[list[float]] = None
    detect_confidence: float = 1.0
    tags: list[str] = Field(default_factory=list)
    note: str = ""
    timestamp: Optional[float] = None
    image: Optional[str] = Field(default=None, description="base64/data-uri/path/url")


class PlaceMemoryUpsertRequest(BaseModel):
    place_name: str
    robot_id: str
    robot_type: str
    place_pose: Pose
    alias: list[str] = Field(default_factory=list)
    note: str = ""
    timestamp: Optional[float] = None
    image: Optional[str] = Field(
        default=None,
        description="Reference image encoded as base64/data-uri/path/url",
    )
    image_captured_at: Optional[float] = Field(
        default=None,
        description="Unix timestamp when the reference image was captured",
    )
    task_description: str = Field(
        default="",
        description="Description of the place or future visual arrival-verification task",
    )


VisualIndexStatus = Literal["not_indexed", "pending", "indexed", "failed", "deleted"]


class VisualIndexUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VisualIndexStatus
    image_id: Optional[str] = None
    image_sha256: Optional[str] = None
    backend: Optional[str] = Field(default=None, max_length=100)
    version: Optional[str] = Field(default=None, max_length=100)
    error: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("image_sha256")
    @classmethod
    def validate_image_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("image_sha256 must be a 64-character hexadecimal SHA-256")
        return normalized

    @field_validator("image_id", "backend", "version")
    @classmethod
    def reject_blank_strings(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_indexed_metadata(self) -> "VisualIndexUpdateRequest":
        if self.status == "indexed" and (not self.backend or not self.version):
            raise ValueError("backend and version are required when status is indexed")
        return self


class SemanticFrameIngestRequest(BaseModel):
    robot_id: str
    robot_type: str
    robot_pose: Pose
    source: str = "camera"
    task_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    note: str = ""
    timestamp: Optional[float] = None
    image: str = Field(..., description="base64/data-uri/path/url")


class KeyframeItem(BaseModel):
    camera_source: str
    rank: Optional[int] = None
    score: float = 0.0
    timestamp: Optional[float] = None
    timestamp_ns: Optional[int] = None
    robot_id: str
    robot_type: str
    pose: Pose
    note: str = ""
    image: str


class KeyframeBatchIngestRequest(BaseModel):
    task_id: str
    items: list[KeyframeItem]


class TaskCreateRequest(BaseModel):
    task_name: Literal["offline_keyframe_pipeline"]
    input_uri: str
    robot_id: str
    robot_type: str
    options: dict[str, Any] = Field(default_factory=dict)


class NameQuery(BaseModel):
    name: str
    n_results: int = 10
    robot_id: Optional[str] = None


class PositionQuery(BaseModel):
    x: float
    y: float
    radius: float = 1.5
    n_results: int = 20
    memory_type: Optional[Literal["object", "place", "keyframe", "semantic_frame"]] = None


class SemanticTextQuery(BaseModel):
    text: str
    n_results: int = 10
    memory_type: Optional[Literal["object", "place", "keyframe", "semantic_frame"]] = None


class UnifiedQuery(BaseModel):
    text: Optional[str] = None
    object_name: Optional[str] = None
    place_name: Optional[str] = None
    robot_id: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    radius: float = 2.0
    memory_type: Optional[Literal["object", "place", "keyframe", "semantic_frame"]] = None
    n_results: int = 20


class MemoryResult(BaseModel):
    id: str
    memory_type: str
    name: str
    robot_id: str
    robot_type: str
    target_pose: Pose
    robot_pose: Optional[Pose] = None
    source: str
    timestamp: float
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    place_id: Optional[str] = None
    image_id: Optional[str] = None
    image_sha256: Optional[str] = None
    image_url: Optional[str] = None
    visual_index: Optional[dict[str, Any]] = None


class GenericResultsResponse(BaseModel):
    results: list[MemoryResult]
