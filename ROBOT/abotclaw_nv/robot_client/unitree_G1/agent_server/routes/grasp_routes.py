"""HTTP 封装：``g1_grasp_sdk.grasp_target``（左右末端目标位置 → 完整抓取序列）。"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/grasp", tags=["G1 Grasp"])


class GraspTargetRequest(BaseModel):
    right_pos: list[float] = Field(..., description="右手末端位置 xyz (m)")
    left_pos: list[float] = Field(..., description="左手末端位置 xyz (m)")
    robot_ip: str = Field(
        default=os.environ.get("G1_ROBOT_IP", "192.168.123.164"),
        description="灵巧手所在机器人 IP",
    )

    @field_validator("right_pos", "left_pos")
    @classmethod
    def _len3(cls, v: list[float]) -> list[float]:
        if len(v) != 3:
            raise ValueError("position must be a length-3 list [x, y, z]")
        return v


class GraspTargetResponse(BaseModel):
    success: bool
    message: str


@router.post("/target", response_model=GraspTargetResponse)
async def post_grasp_target(body: GraspTargetRequest) -> GraspTargetResponse:
    """调用 ``grasp_target``：姿态使用 SDK 内部默认，仅传入左右目标位置。"""
    from robot_sdk.g1_grasp_sdk import grasp_target

    try:
        ok = grasp_target(body.right_pos, body.left_pos, robot_ip=body.robot_ip)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    if ok:
        return GraspTargetResponse(success=True, message="Grasp sequence completed")
    return GraspTargetResponse(success=False, message="Grasp sequence failed")
