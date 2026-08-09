"""机器人微调位置 SDK：通过 LocoClient 发送速度指令，将机器人微调至目标点。

用法示例：
    from robot_sdk import AdjustPosiSDK

    sdk = AdjustPosiSDK()

    # 直接指定坐标
    ok = sdk.adjust(x=1.0, y=2.0, yaw=0.5)

    # 从记忆（MemorySDK）中查询目标位置
    ok = sdk.adjust_by_memory("卧室")

    # 直接按名称查询 + 微调（内部调用 adjust_by_memory）
    ok = sdk.adjust_by_name("厨房")

    print("到位" if ok else "未到位")
"""

import math
import os
from typing import Optional

import rclpy
from rclpy.node import Node

from robot_sdk import Nav2Anywhere
from robot_sdk.config import G1_DEFAULT_NETWORK_INTERFACE

DIST_THRESHOLD = 0.13
YAW_THRESHOLD = math.radians(15.0)
MAX_SPEED = 0.3
MIN_SPEED = 0.1


def _yaw_from_quat(ori) -> float:
    x, y, z, w = ori.x, ori.y, ori.z, ori.w
    return math.atan2(2 * (w * z + x * y), w * w - x * x - y * y + z * z)


def _angle_diff(a: float, b: float) -> float:
    d = a - b
    return math.atan2(math.sin(d), math.cos(d))


def _speed(dist: float) -> float:
    return max(MIN_SPEED, min(MAX_SPEED, dist * (MAX_SPEED - MIN_SPEED) / 0.5))


class AdjustPosiSDK(Node):
    def __init__(self, iface: Optional[str] = None, timeout: float = 60.0):
        super().__init__("adjust_posi_sdk")
        self._iface = iface or os.environ.get("UNITREE_IFACE") or G1_DEFAULT_NETWORK_INTERFACE
        self._loco_client = None
        self._nav = Nav2Anywhere()
        self._mem = None
        self._timeout = timeout

        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.loco.g1_loco_client import G1LocoClient

            ChannelFactoryInitialize(0, self._iface)
            self._loco_client = G1LocoClient()
            self._loco_client.SetTimeout(10.0)
            self._loco_client.Init()
        except Exception as e:
            self.get_logger().warn(f"LocoClient init failed: {e}")

        try:
            from robot_sdk.memory_sdk import MemorySDK
            self._mem = MemorySDK()
        except Exception as e:
            self.get_logger().warn(f"MemorySDK init failed: {e}")

    # ------------------------------------------------------------------ #
    # 公开 API                                                            #
    # ------------------------------------------------------------------ #

    def get_current_pose(self):
        rclpy.spin_once(self._nav, timeout_sec=0.1)
        return self._nav.get_current_pose()

    def adjust(self, x: float, y: float, yaw: float) -> bool:
        """微调机器人至目标点 (x, y, yaw)。

        Args:
            x:   目标 x 坐标 (m)
            y:   目标 y 坐标 (m)
            yaw: 目标 yaw 角 (rad)

        Returns:
            True  到达目标点（距离和角度均进入阈值）
            False 未到达或超时
        """
        if self._loco_client is None:
            self.get_logger().error("LocoClient unavailable")
            return False

        deadline = self.get_clock().now() + rclpy.duration.Duration(seconds=self._timeout)

        while rclpy.ok():
            if self.get_clock().now() >= deadline:
                self._loco_client.StopMove()
                self.get_logger().warn("adjust timed out")
                return False

            pose = self.get_current_pose()
            if pose is None:
                rclpy.spin_once(self, timeout_sec=0.1)
                continue

            cx, cy = pose.pose.position.x, pose.pose.position.y
            cyaw = _yaw_from_quat(pose.pose.orientation)
            dist = math.hypot(cx - x, cy - y)
            yaw_err = _angle_diff(yaw, cyaw)

            self.get_logger().info(f"dist={dist:.3f}m  yaw_err={math.degrees(yaw_err):.1f}deg")

            if dist < DIST_THRESHOLD and abs(yaw_err) < YAW_THRESHOLD:
                self._loco_client.StopMove()
                self.get_logger().info("到位")
                return True

            spd = _speed(dist)

            if abs(yaw_err) > YAW_THRESHOLD:
                self._loco_client.Move(0.0, 0.0, 0.3 if yaw_err > 0 else -0.3)
            else:
                target_yaw = math.atan2(y - cy, x - cx)
                err = _angle_diff(target_yaw, cyaw)
                if abs(err) > YAW_THRESHOLD:
                    self._loco_client.Move(0.0, 0.0, 0.3 if err > 0 else -0.3)
                else:
                    self._loco_client.Move(spd * math.cos(err), spd * math.sin(err), 0.0)

            rclpy.spin_once(self, timeout_sec=0.1)

        self._loco_client.StopMove()
        return False

    def adjust_by_memory(self, name: str, yaw: Optional[float] = None) -> bool:
        """从 Spatial Memory Hub 查询地点，直接微调过去。

        Args:
            name: 记忆中的地点名称
            yaw:  可选，指定目标朝向；None 则使用记忆中存储的 yaw

        Returns:
            True  到达；False 未到达或查询失败
        """
        if self._mem is None:
            self.get_logger().error("MemorySDK unavailable")
            return False

        results = self._mem.query_place(name, n_results=1)
        if not results:
            self.get_logger().error(f"No place found for: {name}")
            return False

        place = results[0]
        pose_dict = place.get("target_pose") or place.get("place_pose") or {}
        tx = pose_dict.get("x", 0.0)
        ty = pose_dict.get("y", 0.0)
        tyaw = yaw if yaw is not None else pose_dict.get("yaw", 0.0)

        self.get_logger().info(f"Memory [{name}]: x={tx:.3f} y={ty:.3f} yaw={tyaw:.3f}")
        return self.adjust(x=tx, y=ty, yaw=tyaw)

    # 别名，方便调用
    def adjust_by_name(self, name: str, yaw: Optional[float] = None) -> bool:
        """adjust_by_memory 的别名。"""
        return self.adjust_by_memory(name, yaw)


# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    rclpy.init()
    sdk = AdjustPosiSDK()

    # 示例：从记忆中查询 "卧室" 并微调
    ok = sdk.adjust_by_memory("卧室")
    print("到位" if ok else "未到位")

    rclpy.shutdown()
