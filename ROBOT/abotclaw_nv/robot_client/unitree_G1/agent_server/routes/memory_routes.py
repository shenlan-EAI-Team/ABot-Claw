"""HTTP 封装：``memory_sdk.MemorySDK``（空间记忆服务）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/memory", tags=["G1 Spatial Memory"])


class PoseModel(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    frame_id: str = "map"


class MemoryHealthResponse(BaseModel):
    status: str
    base_url: str
    message: str | None = None


class UpsertObjectRequest(BaseModel):
    object_name: str = Field(..., description="物体名称")
    robot_id: str = Field(..., description="机器人 ID")
    robot_type: str = Field(default="humanoid", description="机器人类型")
    robot_pose: PoseModel = Field(..., description="机器人位姿")
    object_pose: PoseModel = Field(..., description="物体位姿")
    detect_confidence: float = Field(default=0.0, description="检测置信度")
    image_b64: str | None = Field(default=None, description="图像 base64")


class UpsertObjectResponse(BaseModel):
    ok: bool
    id: str | None = None
    message: str


class QueryObjectRequest(BaseModel):
    name: str = Field(..., description="查询名称")
    n_results: int = Field(default=5, ge=1, le=50)


class QueryObjectResponse(BaseModel):
    results: list[dict]


class UpsertPlaceRequest(BaseModel):
    place_name: str = Field(..., description="地点名称")
    robot_id: str = Field(..., description="机器人 ID")
    robot_type: str = Field(default="humanoid", description="机器人类型")
    place_pose: PoseModel = Field(..., description="地点位姿")
    alias: list[str] = Field(default=[], description="别名列表")
    note: str = Field(default="", description="备注")


class UpsertPlaceResponse(BaseModel):
    ok: bool
    id: str | None = None
    message: str


class QueryPlaceRequest(BaseModel):
    name: str = Field(..., description="查询名称")
    n_results: int = Field(default=10, ge=1, le=50)
    robot_id: str | None = Field(default=None, description="过滤机器人 ID")


class QueryPlaceResponse(BaseModel):
    results: list[dict]


@router.get("/health", response_model=MemoryHealthResponse)
async def memory_health():
    """检查空间记忆服务健康状态。"""
    from robot_sdk import MemorySDK

    try:
        mem = MemorySDK()
        health = mem.health()
        return MemoryHealthResponse(
            status=health.get("status", "unknown"),
            base_url=health.get("base_url", ""),
            message=health.get("error") if health.get("status") == "error" else None,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/object/upsert", response_model=UpsertObjectResponse)
async def upsert_object(body: UpsertObjectRequest):
    """写入或更新物体记忆。"""
    from robot_sdk import MemorySDK

    try:
        mem = MemorySDK()
        result = mem.upsert_object(
            object_name=body.object_name,
            robot_id=body.robot_id,
            robot_type=body.robot_type,
            robot_pose=body.robot_pose.model_dump(),
            object_pose=body.object_pose.model_dump(),
            detect_confidence=body.detect_confidence,
            image_b64=body.image_b64,
        )
        return UpsertObjectResponse(
            ok=result.get("ok", True),
            id=result.get("id"),
            message="Object upserted successfully",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/object/query", response_model=QueryObjectResponse)
async def query_object(body: QueryObjectRequest):
    """查询物体记忆。"""
    from robot_sdk import MemorySDK

    try:
        mem = MemorySDK()
        results = mem.query_object(name=body.name, n_results=body.n_results)
        return QueryObjectResponse(results=results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/place/upsert", response_model=UpsertPlaceResponse)
async def upsert_place(body: UpsertPlaceRequest):
    """写入或更新地点记忆。"""
    from robot_sdk import MemorySDK

    try:
        mem = MemorySDK()
        result = mem.upsert_place(
            place_name=body.place_name,
            robot_id=body.robot_id,
            robot_type=body.robot_type,
            place_pose=body.place_pose.model_dump(),
            alias=body.alias,
            note=body.note,
        )
        return UpsertPlaceResponse(
            ok=result.get("ok", True),
            id=result.get("id"),
            message="Place upserted successfully",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/place/query", response_model=QueryPlaceResponse)
async def query_place(body: QueryPlaceRequest):
    """查询地点记忆。"""
    from robot_sdk import MemorySDK

    try:
        mem = MemorySDK()
        results = mem.query_place(
            name=body.name,
            n_results=body.n_results,
            robot_id=body.robot_id,
        )
        return QueryPlaceResponse(results=results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
