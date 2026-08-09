"""G1 机器人状态聚合器 — 从 G1RobotEnv 采集并维护快照。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class StateAggregator:
    """周期轮询 G1RobotEnv，维护最新的机器人状态快照。

    状态结构:
      {
        "timestamp": float,
        "body": {
          "position": [x, y, z],           # 世界坐标系位置
          "orientation": [qx, qy, qz, qw], # 四元数
          "velocity": [vx, vy, vz],        # 线速度
          "angular_velocity": [wx, wy, wz], # 角速度
        },
        "arm": {
            "joint_positions":  [14 floats]  双臂关节角 (rad)，顺序与 ``robotbridge.JOINTS`` 一致：
                左臂 7 (motor 15–21) + 右臂 7 (motor 22–28)，见 Unitree G1 HG ``LowState`` 索引。
            "joint_velocities": [14 floats]  关节速度 (rad/s),
            "joint_efforts":    [14 floats]  关节力矩 (Nm),
        },
        "imu": {
          "acceleration": [ax, ay, az],     # 加速度
          "angular_velocity": [wx, wy, wz], # 角速度
          "orientation": [qx, qy, qz, qw],  # 姿态四元数
        },
        "status": {
          "connected": bool,       # 是否连接到机器人
          "sport_ready": bool,     # 运动模块就绪
          "arm_ready": bool,       # 手臂模块就绪
        },
      }
    """

    def __init__(self, env=None, poll_hz: float = 10.0, robotbridge=None) -> None:
        """
        Args:
            env: G1RobotEnv 实例（已弃用，使用 robotbridge）
            poll_hz: 轮询频率
            robotbridge: RobotBridge 实例；优先使用
        """
        self._env = env  # Deprecated
        self._robotbridge = robotbridge
        self._poll_hz = poll_hz
        self._state: dict[str, Any] = self._empty_state()
        self._task: Optional[asyncio.Task] = None
        self._last_moved_at: float = 0.0
        self._prev_joint_positions: list[float] = []
        
        if self._robotbridge is not None:
            logger.info("StateAggregator using RobotBridge for state monitoring")
        elif self._env is not None:
            logger.warning("StateAggregator using deprecated G1RobotEnv")
        else:
            logger.info("StateAggregator running without robot connection - state will be empty")

    # ------------------------------------------------------------------ #
    #  Public helpers
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def last_moved_at(self) -> float:
        """返回最近一次检测到机器人运动的时间戳。"""
        return self._last_moved_at

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

        def _poll_done(t: asyncio.Task) -> None:
            try:
                t.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("State poll loop task exited with error")

        self._task.add_done_callback(_poll_done)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "timestamp": 0.0,
            "body": {
                "position": [0.0, 0.0, 0.0],
                "orientation": [0.0, 0.0, 0.0, 1.0],
                "velocity": [0.0, 0.0, 0.0],
                "angular_velocity": [0.0, 0.0, 0.0],
            },
            "arm": {
                "joint_positions": [],
                "joint_velocities": [],
                "joint_efforts": [],
            },
            "imu": {
                "acceleration": [0.0, 0.0, 9.81],
                "angular_velocity": [0.0, 0.0, 0.0],
                "orientation": [0.0, 0.0, 0.0, 1.0],
            },
            "status": {
                "connected": False,
                "sport_ready": False,
                "arm_ready": False,
            },
        }

    def _update_movement(self, joint_positions: list) -> None:
        if joint_positions and self._prev_joint_positions:
            if any(
                abs(a - b) > 0.005
                for a, b in zip(joint_positions, self._prev_joint_positions)
            ):
                self._last_moved_at = time.time()
        if joint_positions:
            self._prev_joint_positions = list(joint_positions)

    async def _poll_loop(self) -> None:
        interval = 1.0 / self._poll_hz
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("State poll error")
            await asyncio.sleep(interval)

    async def _poll_once(self) -> None:
        # Priority: RobotBridge > G1RobotEnv (deprecated) > empty state
        if self._robotbridge is not None:
            await self._poll_via_robotbridge()
        elif self._env is not None:
            await self._poll_via_env()
        else:
            self._state = self._empty_state()
            self._state["timestamp"] = time.time()

    async def _poll_via_robotbridge(self) -> None:
        """Poll state via RobotBridge (preferred)."""
        loop = asyncio.get_event_loop()
        
        try:
            joint_states = await loop.run_in_executor(None, self._robotbridge.get_joint_states)
        except Exception as e:
            logger.debug("RobotBridge get_joint_states failed: %s", e)
            joint_states = None
        
        arm_positions = []
        arm_velocities = []
        arm_efforts = []
        
        if joint_states:
            # 与 ``robot_sdk/robotbridge.py`` 中 ``JOINTS`` / IK 一致：左臂 15–21、右臂 22–28（共 14）
            try:
                from robot_sdk.robotbridge import JOINTS as _RB_JOINTS

                arm_indices = [t[0] for t in _RB_JOINTS]
            except Exception:
                arm_indices = list(range(15, 22)) + list(range(22, 29))
            logger.info("RobotBridge joint_states keys: %s", list(joint_states.keys())[:10])
            for idx in arm_indices:
                if idx in joint_states:
                    arm_positions.append(joint_states[idx]["q"])
                    arm_velocities.append(joint_states[idx]["dq"])
                    arm_efforts.append(joint_states[idx]["tau"])
            
            self._update_movement(arm_positions)
        
        self._state = {
            "timestamp": time.time(),
            "body": self._empty_state()["body"],  # RobotBridge doesn't provide body state yet
            "arm": {
                "joint_positions": arm_positions,
                "joint_velocities": arm_velocities,
                "joint_efforts": arm_efforts,
            },
            "imu": self._empty_state()["imu"],  # RobotBridge doesn't provide IMU yet
            "status": {
                "connected": self._robotbridge.ok,
                "sport_ready": self._robotbridge.ok,
                "arm_ready": self._robotbridge.ok,
            },
        }

    async def _poll_via_env(self) -> None:
        """Poll state via G1RobotEnv (deprecated)."""
        loop = asyncio.get_event_loop()

        # 身体状态
        body_state = None
        try:
            body_state = await loop.run_in_executor(None, self._env.get_body_state)
        except Exception as e:
            logger.debug("get_body_state failed: %s", e)

        # 手臂状态
        arm_state = None
        try:
            arm_state = await loop.run_in_executor(None, self._env.get_arm_state)
        except Exception as e:
            logger.debug("get_arm_state failed: %s", e)

        # IMU 数据
        imu_data = None
        try:
            imu_data = await loop.run_in_executor(None, self._env.get_imu)
        except Exception as e:
            logger.debug("get_imu failed: %s", e)

        # 更新运动检测
        if arm_state:
            self._update_movement(list(arm_state.position))

        self._state = {
            "timestamp": time.time(),
            "body": {
                "position": list(body_state.position) if body_state else [0.0, 0.0, 0.0],
                "orientation": list(body_state.orientation) if body_state else [0.0, 0.0, 0.0, 1.0],
                "velocity": list(body_state.velocity) if body_state else [0.0, 0.0, 0.0],
                "angular_velocity": list(body_state.angular_velocity) if body_state else [0.0, 0.0, 0.0],
            } if body_state else self._empty_state()["body"],
            "arm": {
                "joint_positions": list(arm_state.position) if arm_state else [],
                "joint_velocities": list(arm_state.velocity) if arm_state else [],
                "joint_efforts": list(arm_state.effort) if arm_state else [],
            } if arm_state else self._empty_state()["arm"],
            "imu": imu_data if imu_data else self._empty_state()["imu"],
            "status": {
                "connected": self._env.is_connected,
                "sport_ready": True,
                "arm_ready": True,
            },
        }
