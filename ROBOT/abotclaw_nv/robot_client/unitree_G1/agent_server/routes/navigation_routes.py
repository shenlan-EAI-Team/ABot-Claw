"""HTTP 封装：navigation_sdk.Nav2Anywhere（位姿导航）。"""

from __future__ import annotations

import logging
import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("agent_server.nav")

router = APIRouter(prefix="/nav", tags=["G1 Navigation"])


# ── Request / Response models ────────────────────────────────────────────────

class NavToPoseRequest(BaseModel):
    """带四元数的完整位姿目标（走 move_base action，支持等待完成）。"""
    x: float = Field(..., description="目标 x 坐标")
    y: float = Field(..., description="目标 y 坐标")
    z: float = Field(default=0.0, description="目标 z 坐标")
    frame_id: str = Field(default="map", description="坐标系")
    qx: float | None = Field(default=None, description="四元数 x")
    qy: float | None = Field(default=None, description="四元数 y")
    qz: float | None = Field(default=None, description="四元数 z")
    qw: float | None = Field(default=None, description="四元数 w")
    timeout_sec: float = Field(default=300.0, gt=0.0, description="等待到达的超时秒数")


class NavToPoseResponse(BaseModel):
    success: bool
    message: str
    distance: float | None = None


class SimpleGoalRequest(BaseModel):
    """简单目标：x/y/yaw，直接发 /move_base_simple/goal（fire-and-forget，不阻塞）。

    支持两种朝向输入方式：
    - yaw/pitch/roll：欧拉角（弧度），默认方式
    - qx/qy/qz/qw：四元数，优先级高于欧拉角
    """
    x: float = Field(..., description="目标 x 坐标")
    y: float = Field(..., description="目标 y 坐标")
    z: float = Field(default=0.0, description="目标 z 坐标")
    yaw: float = Field(default=0.0, description="朝向偏航角（弧度），qx~qw 存在时被忽略")
    pitch: float = Field(default=0.0, description="俯仰角（弧度）")
    roll: float = Field(default=0.0, description="翻滚角（弧度）")
    frame_id: str = Field(default="map", description="坐标系")
    qx: float | None = Field(default=None, description="四元数 x，优先级高于 yaw")
    qy: float | None = Field(default=None, description="四元数 y")
    qz: float | None = Field(default=None, description="四元数 z")
    qw: float | None = Field(default=None, description="四元数 w")


class SimpleGoalResponse(BaseModel):
    success: bool
    message: str
    goal_x: float
    goal_y: float
    goal_yaw: float
    goal_qx: float | None = None
    goal_qy: float | None = None
    goal_qz: float | None = None
    goal_qw: float | None = None
    frame_id: str


class CurrentPoseResponse(BaseModel):
    x: float
    y: float
    z: float
    yaw: float = Field(..., description="绕 z 轴偏航角（弧度）")
    qx: float
    qy: float
    qz: float
    qw: float
    frame_id: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/simple_goal", response_model=SimpleGoalResponse)
async def navigate_simple(body: SimpleGoalRequest) -> SimpleGoalResponse:
    """
    推荐方式：发 /move_base_simple/goal（fire-and-forget），立即返回。

    机器人端收到后自行规划执行，不等待结果。
    适合远程控制或实时下发连续目标。
    """
    from robot_sdk.navigation_sdk import Nav2Anywhere

    try:
        node = Nav2Anywhere()
        node.publish_simple_goal(
            x=body.x,
            y=body.y,
            z=body.z,
            yaw=body.yaw,
            pitch=body.pitch,
            roll=body.roll,
            frame_id=body.frame_id,
            qx=body.qx,
            qy=body.qy,
            qz=body.qz,
            qw=body.qw,
        )
        has_quat = None not in (body.qx, body.qy, body.qz, body.qw)
        mode_str = "quat" if has_quat else "euler"
        logger.info(
            "simple_goal: (%.3f, %.3f) %s yaw=%.3f frame=%s",
            body.x, body.y, mode_str, body.yaw, body.frame_id,
        )
        has_quat = None not in (body.qx, body.qy, body.qz, body.qw)
        return SimpleGoalResponse(
            success=True,
            message="Goal published to /move_base_simple/goal",
            goal_x=body.x,
            goal_y=body.y,
            goal_yaw=body.yaw,
            goal_qx=body.qx if has_quat else None,
            goal_qy=body.qy if has_quat else None,
            goal_qz=body.qz if has_quat else None,
            goal_qw=body.qw if has_quat else None,
            frame_id=body.frame_id,
        )
    except Exception as e:
        logger.error("simple_goal failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/to_pose", response_model=NavToPoseResponse)
async def navigate_to_pose(body: NavToPoseRequest) -> NavToPoseResponse:
    """
    Action 方式：发 move_base action，通过 wait_until_reached 阻塞等待到达或超时。

    适用于需要确认到达结果的场景。
    """
    from geometry_msgs.msg import PoseStamped
    from robot_sdk.navigation_sdk import Nav2Anywhere

    try:
        has_quat = None not in (body.qx, body.qy, body.qz, body.qw)
        pose = PoseStamped()
        pose.header.frame_id = body.frame_id
        pose.pose.position.x = body.x
        pose.pose.position.y = body.y
        pose.pose.position.z = body.z
        if has_quat:
            pose.pose.orientation.x = float(body.qx)  # type: ignore[arg-type]
            pose.pose.orientation.y = float(body.qy)  # type: ignore[arg-type]
            pose.pose.orientation.z = float(body.qz)  # type: ignore[arg-type]
            pose.pose.orientation.w = float(body.qw)  # type: ignore[arg-type]
        else:
            pose.pose.orientation.w = 1.0

        node = Nav2Anywhere()
        logger.info(
            "nav_to_pose: (%.2f, %.2f) frame=%s timeout=%.1fs",
            body.x, body.y, body.frame_id, body.timeout_sec,
        )
        if not node.nav_to_pose(pose):
            return NavToPoseResponse(
                success=False,
                message="Failed to send goal to move_base",
            )

        reached = node.wait_until_reached(timeout_sec=body.timeout_sec)
        current = node.get_current_pose()
        distance: float | None = None
        if current is not None:
            distance = math.hypot(
                current.pose.position.x - pose.pose.position.x,
                current.pose.position.y - pose.pose.position.y,
            )

        if reached:
            logger.info("nav_to_pose: reached, dist=%.3fm", distance)
            return NavToPoseResponse(success=True, message="Goal reached", distance=distance)

        logger.warning("nav_to_pose: timeout %.1fs dist=%.3fm", body.timeout_sec, distance or -1)
        return NavToPoseResponse(
            success=False,
            message=f"Timeout after {body.timeout_sec:.1f}s",
            distance=distance,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/current_pose", response_model=CurrentPoseResponse)
async def get_current_pose() -> CurrentPoseResponse:
    """获取当前机器人位姿（含偏航角与原始四元数）。"""
    from robot_sdk.navigation_sdk import Nav2Anywhere, _yaw_from_quaternion

    try:
        node = Nav2Anywhere()
        pose = node.get_current_pose()
        if pose is None:
            raise HTTPException(status_code=503, detail="Waiting for odometry")
        ori = pose.pose.orientation
        return CurrentPoseResponse(
            x=pose.pose.position.x,
            y=pose.pose.position.y,
            z=pose.pose.position.z,
            yaw=_yaw_from_quaternion(ori),
            qx=float(ori.x),
            qy=float(ori.y),
            qz=float(ori.z),
            qw=float(ori.w),
            frame_id=pose.header.frame_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
