"""
空间记忆 SDK — G1 版 (HTTP API)

通过 Spatial Memory Hub HTTP 服务完成物体记忆和地点记忆的写入与查询。
无 ROS 依赖，纯 HTTP 客户端。

配置优先级: 构造函数参数 > 环境变量 SPATIAL_MEMORY_HUB_URL > config.yaml > 内置默认值

暴露接口:
    - upsert_object(...)   -> dict   写入/更新物体记忆
    - query_object(...)    -> list    按名称查询物体
    - upsert_place(...)    -> dict   写入/更新地点记忆
    - query_place(...)     -> list    按名称查询地点
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

try:
    from .config import get_config
except ImportError:
    # Fallback for direct module import (code execution context)
    from config import get_config


@dataclass
class Pose:
    """6-DoF 位姿"""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    frame_id: str = "map"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "roll": self.roll,
            "pitch": self.pitch,
            "yaw": self.yaw,
            "frame_id": self.frame_id,
        }


class MemorySDK:
    """
    Spatial Memory Hub 记忆封装（物体 + 地点）

    提供物体记忆和地点记忆的写入 (upsert) 与查询 (query) 能力。
    所有参数均可省略，默认从 config.yaml / 环境变量读取。

    用法:
        mem = MemorySDK()

        # ---- 物体记忆 ----
        result = mem.upsert_object(
            object_name="red_cup",
            robot_id="g1_001",
            robot_type="humanoid",
            robot_pose=Pose(x=1.0, y=1.0),
            object_pose=Pose(x=1.2, y=1.1, z=0.8),
            detect_confidence=0.92,
        )
        print(result)  # {"ok": True, "id": "abc123"}

        results = mem.query_object("red_cup", n_results=5)
        for r in results:
            print(r["name"], r["pose"])

        # ---- 地点记忆 ----
        result = mem.upsert_place(
            place_name="卧室",
            robot_id="g1_001",
            robot_type="humanoid",
            place_pose=Pose(x=-1.34, y=-0.12, yaw=-0.16),
            note="卧室门口",
        )
        print(result)  # {"ok": True, "id": "plc_..."}

        results = mem.query_place("卧室", n_results=5)
        for r in results:
            print(r["name"], r["target_pose"])
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ):
        cfg = get_config()
        mem_cfg = cfg.get("spatial_memory", {})

        self._base_url = (
            base_url
            or os.environ.get("SPATIAL_MEMORY_HUB_URL")
            or mem_cfg.get("url", "http://127.0.0.1:8022")
        ).rstrip("/")
        self._timeout = (
            request_timeout
            if request_timeout is not None
            else mem_cfg.get("request_timeout", 10.0)
        )

    def health(self) -> Dict[str, Any]:
        """检查 Spatial Memory Hub 连通性"""
        try:
            resp = requests.get(
                f"{self._base_url}/health", timeout=self._timeout,
            )
            resp.raise_for_status()
            return {"status": "ok", "base_url": self._base_url}
        except Exception as exc:
            return {"status": "error", "base_url": self._base_url, "error": str(exc)}

    # ================================================================== #
    #                       内部工具
    # ================================================================== #

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = requests.post(
            f"{self._base_url}{path}",
            json=payload,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ================================================================== #
    #                       公开 API
    # ================================================================== #

    def upsert_object(
        self,
        object_name: str,
        robot_id: str,
        robot_type: str,
        robot_pose: Pose | Dict[str, Any],
        object_pose: Pose | Dict[str, Any],
        detect_confidence: float = 0.0,
        image_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        写入或更新一个物体记忆

        Args:
            object_name:        物体名称，如 "red_cup"
            robot_id:           机器人 ID，如 "g1_001"
            robot_type:         机器人类型，如 "humanoid"
            robot_pose:         观测时机器人位姿 (Pose 或 dict)
            object_pose:        物体位姿 (Pose 或 dict)
            detect_confidence:  检测置信度 (0~1)
            image_b64:          可选，base64 编码的物体图像

        Returns:
            dict: {"ok": True, "id": "..."}
        """
        payload: Dict[str, Any] = {
            "object_name": object_name,
            "robot_id": robot_id,
            "robot_type": robot_type,
            "robot_pose": robot_pose.to_dict() if isinstance(robot_pose, Pose) else robot_pose,
            "object_pose": object_pose.to_dict() if isinstance(object_pose, Pose) else object_pose,
            "detect_confidence": detect_confidence,
        }
        if image_b64 is not None:
            payload["image"] = image_b64

        data = self._post("/memory/object/upsert", payload)
        if not data.get("ok"):
            raise RuntimeError(f"upsert_object 失败: {data}")
        return data

    def insert_object(
        self,
        object_name: str,
        robot_id: str,
        robot_type: str,
        robot_pose: Pose | Dict[str, Any],
        object_pose: Pose | Dict[str, Any],
        detect_confidence: float = 0.0,
        image_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """插入（别名 upsert）：写入/更新一个物体记忆"""
        return self.upsert_object(
            object_name=object_name,
            robot_id=robot_id,
            robot_type=robot_type,
            robot_pose=robot_pose,
            object_pose=object_pose,
            detect_confidence=detect_confidence,
            image_b64=image_b64,
        )

    def query_object(
        self,
        name: str,
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        按名称查询物体记忆

        Args:
            name:       要查询的物体名称（支持模糊匹配）
            n_results:  最多返回条数

        Returns:
            list[dict]: 匹配的物体记录列表
        """
        data = self._post("/query/object", {"name": name, "n_results": n_results})
        return data.get("results", [])

    # ================================================================== #
    #                       地点记忆 API
    # ================================================================== #

    def upsert_place(
        self,
        place_name: str,
        robot_id: str,
        robot_type: str,
        place_pose: Pose | Dict[str, Any],
        alias: Optional[List[str]] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        """
        写入或更新一个地点记忆

        Args:
            place_name:  地点名称，如 "卧室", "厨房"
            robot_id:    机器人 ID，如 "g1_001"
            robot_type:  机器人类型，如 "humanoid"
            place_pose:  地点位姿 (Pose 或 dict)
            alias:       别名列表，如 ["bedroom", "睡房"]
            note:        备注说明

        Returns:
            dict: {"ok": True, "id": "plc_..."}
        """
        payload: Dict[str, Any] = {
            "place_name": place_name,
            "robot_id": robot_id,
            "robot_type": robot_type,
            "place_pose": place_pose.to_dict() if isinstance(place_pose, Pose) else place_pose,
            "alias": alias or [],
            "note": note,
        }
        data = self._post("/memory/place/upsert", payload)
        if not data.get("ok"):
            raise RuntimeError(f"upsert_place 失败: {data}")
        return data

    def query_place(
        self,
        name: str,
        n_results: int = 10,
        robot_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        按名称查询地点记忆

        Args:
            name:       要查询的地点名称（支持模糊匹配）
            n_results:  最多返回条数
            robot_id:   可选，按机器人 ID 过滤

        Returns:
            list[dict]: 匹配的地点记录列表，每条包含 name / target_pose / timestamp 等
        """
        payload: Dict[str, Any] = {"name": name, "n_results": n_results}
        if robot_id is not None:
            payload["robot_id"] = robot_id
        data = self._post("/query/place", payload)
        return data.get("results", [])

    def stop(self) -> None:
        """无操作 (纯 HTTP 客户端无需清理)"""
        pass
