"""G1 固定轨迹抓取（内部实现）。

对外仅暴露 ``grasp_target``：根据左右末端**目标位置**执行完整抓取序列；姿态沿用内部默认 target。

**控制路径（为何「只有手动、手臂不动」）**

- **双臂**：``G1IKController`` → ``RobotBridge`` → DDS 发布 ``rt/arm_sdk``（需本机安装
  ``unitree_sdk2``、``IKConfig.iface`` 网卡与机器人二层互通、机器人侧进入可接受 arm_sdk 的模式）。
- **灵巧手**：``send_hand_command`` → TCP ``robot_ip:hand_port``（默认 5678），与 DDS **独立**；
  手能闭合不代表 DDS 或 arm_sdk 一定生效。

若手臂不响应，请查控制台是否出现 ``[G1IKController] RobotBridge initialization failed``、
``[robot] SDK-2 not present`` 或 ``DDS init failed``；并确认运动模式未占用手臂（必要时用官方
MotionSwitcher 切到 arm 控制）。Agent 侧 **state** 中 ``arm.joint_positions`` 须与
``robotbridge.JOINTS`` 索引一致（左 15–21、右 22–28），否则反馈会误判。

**逐项排查环境变量（子进程需与 shell 一致传入）**：

- ``G1_ARM_DEBUG=1``：打印 LowState 等待结果、首次 ``send_qpos_tau`` 是否发出。
- ``G1_ARM_LOWSTATE_TIMEOUT``：等待首帧 ``rt/lowstate`` 的秒数（默认 10）。
- ``G1_ARM_ALLOW_NO_LOWSTATE=1``：无 LowState 仍初始化且 **仍写** ``rt/arm_sdk``（腿腰未镜像，真机慎用；用于区分「门控阻塞」与「DDS/模式」问题）。
- ``G1_ARM_SKIP_CHECK_MODE=1``：跳过 ``MotionSwitcher.CheckMode()``，与部分 main 脚本行为对比。
- ``G1_ARM_PD_KP`` / ``G1_ARM_PD_KD``：覆盖默认 PD，便于与 AbotClaw ``main.py`` 对齐试验。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence

import numpy as np

from robot_sdk.hand_sdk import send_hand_command
from robot_sdk.config import get_g1_robot_ip

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

if TYPE_CHECKING:
    from utils.ik import G1IKController

__all__ = ["detect_grasp_target", "grasp_target", "release_object", "detect_grasp_target_anygrasp"]

_Q_I = np.array([0.0, 0.0, 0.0, 1.0], dtype=float)

# 抓取参考（如感知/示教）常用「base 系」：相对 torso 系，base 原点沿竖直方向在 torso 下方约 0.044 m。
# IK / ``move_to_waypoint`` 使用与 MuJoCo 一致的 torso 系末端目标；将用户给出的 z 减去该值，对齐到 torso。
_GRASP_REF_BASE_BELOW_TORSO_Z_M: float = 0.044

# 抓取后仅小幅抬高右手，便于 VLAC 在目标仍处于 D435i 视野时取得 After。
_VLAC_VERIFY_LIFT_Z_M: float = 0.04


def _wp(rp, rq, lp, lq) -> dict:
    return {
        "right_pos": np.asarray(rp, dtype=float),
        "right_quat": np.asarray(rq, dtype=float),
        "left_pos": np.asarray(lp, dtype=float),
        "left_quat": np.asarray(lq, dtype=float),
    }


_DEFAULT_TARGET_WAYPOINT = _wp(
    [0.471, -0.0074, 0.022],
    [0.0, 0.0, 0.2588, 0.96],
    [-0.003, 0.212, -0.004],
    _Q_I,
)

_NAMED_WAYPOINTS = {
    "home": _wp(
        [0.001, -0.242, -0.204],
        [-0.129, 0.622, 0.051, 0.771],
        [0.001, 0.212, -0.204],
        [-0.129, 0.622, 0.051, 0.771],
    ),
    "lift": _wp([0.0, -0.262, 0.10], _Q_I,  [0.001, 0.212, -0.204], [-0.129, 0.622, 0.051, 0.771]),
    "lift_return": _wp([-0.035, -0.35, 0.05], _Q_I,  [0.001, 0.212, -0.204], [-0.129, 0.622, 0.051, 0.771]),
    "lift_relese": _wp([0.2, -0.262, 0.10], _Q_I,  [0.001, 0.212, -0.204], [-0.129, 0.622, 0.051, 0.771]),

}

_DEFAULT_LEFT_HOLD_POS = (0.001, 0.212, -0.204)


def _copy_waypoint(wp: dict) -> dict:
    return {
        "right_pos": np.asarray(wp["right_pos"], dtype=float).copy(),
        "right_quat": np.asarray(wp["right_quat"], dtype=float).copy(),
        "left_pos": np.asarray(wp["left_pos"], dtype=float).copy(),
        "left_quat": np.asarray(wp["left_quat"], dtype=float).copy(),
    }


def _normalize_target_waypoint(d: dict) -> dict:
    for k in ("right_pos", "right_quat", "left_pos", "left_quat"):
        if k not in d:
            raise KeyError(f"target waypoint missing key: {k}")
    return _wp(d["right_pos"], d["right_quat"], d["left_pos"], d["left_quat"])


def _default_grasp_iface() -> str:
    """DDS 网卡；可用环境变量 ``G1_ARM_NETWORK_IFACE`` / ``G1_ARM_NETWORK_INTERFACE`` / ``UNITREE_IFACE`` 覆盖。"""
    return os.environ.get(
        "G1_ARM_NETWORK_IFACE",
        os.environ.get(
            "G1_ARM_NETWORK_INTERFACE",
            os.environ.get("UNITREE_IFACE", os.environ.get("G1_NETWORK_INTERFACE", "enp4s0")),
        ),
    )


@dataclass
class _GraspConfig:
    robot_ip: str = field(default_factory=get_g1_robot_ip)
    hand_port: int = 5678
    iface: str = field(default_factory=_default_grasp_iface)
    kp: float = 60.0
    kd: float = 1.5
    duration_per_waypoint: float = 3.0
    grasp_command: str = "11"
    release_command: str = "7"
    pre_grasp_delay: float = 0.5
    post_grasp_delay: float = 1.0
    target_waypoint: Optional[dict] = None


class _G1LiftController:
    def __init__(self, config: Optional[_GraspConfig] = None):
        self.config = config or _GraspConfig()
        self.ik_controller: Optional["G1IKController"] = None
        self._initialized = False
        self._target_runtime_override: Optional[dict] = None

    def initialize(self) -> bool:
        from utils.ik import G1IKController, IKConfig

        try:
            self.ik_controller = G1IKController(
                config=IKConfig(
                    iface=self.config.iface,
                    kp=self.config.kp,
                    kd=self.config.kd,
                ),
                enable_robot=True,
            )
            if not self.ik_controller.initialize():
                print("[G1LiftController] Failed to initialize IK controller")
                return False
            self._initialized = True
            return True
        except Exception as e:
            print(f"[G1LiftController] Initialization failed: {e}")
            return False

    def _check_init(self) -> bool:
        if self._initialized:
            return True
        print("[G1LiftController] Not initialized")
        return False

    def _move_wp(self, wp: dict) -> bool:
        return self.ik_controller.move_to_waypoint(
            wp["right_pos"], wp["right_quat"], wp["left_pos"], wp["left_quat"]
        )

    def _hand(self, command: str) -> None:
        try:
            send_hand_command(command, self.config.robot_ip, self.config.hand_port)
        except Exception as e:
            print(f"[G1LiftController] Hand command failed: {e}")

    def set_target_waypoint(self, waypoint: Optional[dict]) -> None:
        self._target_runtime_override = (
            _copy_waypoint(_normalize_target_waypoint(waypoint)) if waypoint is not None else None
        )

    def get_effective_target_waypoint(self) -> dict:
        if self._target_runtime_override is not None:
            return _copy_waypoint(self._target_runtime_override)
        if self.config.target_waypoint is not None:
            return _copy_waypoint(_normalize_target_waypoint(self.config.target_waypoint))
        return _copy_waypoint(_DEFAULT_TARGET_WAYPOINT)

    def move_to_named_waypoint(self, waypoint_name: str) -> bool:
        if not self._check_init():
            return False
        if waypoint_name == "forward":
            waypoint_name = "target"
        if waypoint_name == "target":
            return self._move_wp(self.get_effective_target_waypoint())
        if waypoint_name not in _NAMED_WAYPOINTS:
            print(f"[G1LiftController] Unknown waypoint: {waypoint_name}")
            return False
        return self._move_wp(_NAMED_WAYPOINTS[waypoint_name])

    def execute_grasp_sequence(
        self,
        target_waypoint: Optional[dict] = None,
        after_lift_callback: Optional[Callable[[], None]] = None,
    ) -> bool:
        if not self._check_init():
            return False
        try:

            def step(n: int, desc: str) -> None:
                print(f"\n[Grasp Sequence] Step {n}: {desc}")

            step(1, "Move to home position")
            if not self.move_to_named_waypoint("home"):
                return False
            time.sleep(self.config.pre_grasp_delay)

            step(2, "Move to lift position")
            if not self.move_to_named_waypoint("lift"):
                return False

            step(3, "Move to target position")
            wp = (
                _copy_waypoint(_normalize_target_waypoint(target_waypoint))
                if target_waypoint is not None
                else self.get_effective_target_waypoint()
            )
            if not self._move_wp(wp):
                return False

            step(4, "Grasp")
            self._hand(self.config.grasp_command)
            time.sleep(self.config.post_grasp_delay)

            print("\n[Grasp Sequence] Step 4.5: Small lift for VLAC verification")
            verify_wp = _copy_waypoint(wp)
            verify_wp["right_pos"][2] += _VLAC_VERIFY_LIFT_Z_M
            if not self._move_wp(verify_wp):
                return False

            if after_lift_callback is not None:
                after_lift_callback()

            step(5, "Return to lift_return position")
            if not self.move_to_named_waypoint("lift_return"):
                return False
            # step(6, "Return to lift position")
            # if not self.move_to_named_waypoint("lift"):
            #     return False

            step(6, "Return to home position")
            if not self.move_to_named_waypoint("home"):
                return False

            print("\n[Grasp Sequence] Completed successfully!")
            return True
        except Exception as e:
            print(f"[G1LiftController] Grasp sequence failed: {e}")
            return False

    def execute_pick_and_place(self, pick_waypoint: dict, place_waypoint: dict) -> bool:
        if not self._check_init():
            return False
        try:
            print("\n[Pick&Place] Moving to pick position...")
            if not self._move_wp(pick_waypoint):
                return False
            print("[Pick&Place] Grasping...")
            self._hand(self.config.grasp_command)
            time.sleep(self.config.post_grasp_delay)
            print("\n[Pick&Place] Moving to place position...")
            if not self._move_wp(place_waypoint):
                return False
            print("[Pick&Place] Releasing...")
            self._hand(self.config.release_command)
            print("[Pick&Place] Completed successfully!")
            return True
        except Exception as e:
            print(f"[G1LiftController] Pick and place failed: {e}")
            return False

    def execute_release_sequence(self) -> bool:
        if not self._check_init():
            return False
        try:

            def step(n: int, desc: str) -> None:
                print(f"\n[Release Sequence] Step {n}: {desc}")

            step(1, "Move to lift_relese position")
            if not self.move_to_named_waypoint("lift_relese"):
                return False

            step(2, "Release")
            self._hand(self.config.release_command)
            time.sleep(self.config.pre_grasp_delay)

            step(3, "Return to home position")
            if not self.move_to_named_waypoint("home"):
                return False

            print("\n[Release Sequence] Completed successfully!")
            return True
        except Exception as e:
            print(f"[G1LiftController] Release sequence failed: {e}")
            return False

    def move_through_waypoints(self, waypoints: List[dict]) -> bool:
        if not self._check_init():
            return False
        for i, wp in enumerate(waypoints):
            print(f"\n[Waypoints] Moving to waypoint {i + 1}/{len(waypoints)}...")
            if not self._move_wp(wp):
                print(f"[Waypoints] Failed at waypoint {i + 1}")
                return False
        print("[Waypoints] All waypoints completed!")
        return True

    def get_joint_states(self):
        return self.ik_controller.get_joint_states() if self.ik_controller else None

    def close(self) -> None:
        if self.ik_controller:
            self.ik_controller.close()
            self.ik_controller = None
        self._initialized = False


def _with_controller(config: Optional[_GraspConfig], fn: Callable[[_G1LiftController], bool]) -> bool:
    ctrl = _G1LiftController(config or _GraspConfig())
    if not ctrl.initialize():
        return False
    try:
        return fn(ctrl)
    finally:
        ctrl.close()


def _waypoint_from_positions(
    right_pos: Sequence[float],
    left_pos: Sequence[float],
) -> dict:
    """由左右末端位置构造完整 waypoint（四元数取自 `_DEFAULT_TARGET_WAYPOINT`）。

    Args:
        right_pos: 右手目标位置 ``[x, y, z]``（米）。
        left_pos: 左手目标位置 ``[x, y, z]``（米）。

    Note:
        输入位置按「抓取参考系」理解：该系下 base 原点较 torso 系沿竖直方向低
        ``_GRASP_REF_BASE_BELOW_TORSO_Z_M``。IK 使用 torso 系，故本函数对左右 **z**
        各减去该常量后再组 waypoint。
    """
    r = np.asarray(right_pos, dtype=float).reshape(3).copy()
    l = np.asarray(left_pos, dtype=float).reshape(3).copy()
    r[1] -= 0.02
    r[2] -= 0.01
    d = _DEFAULT_TARGET_WAYPOINT
    return _wp(r, d["right_quat"], l, [-0.129, 0.622, 0.051, 0.771])


def grasp_target(
    right_pos: Sequence[float],
    left_pos: Sequence[float] = ([0.05, 0.242, 0.10]),
    *,
    robot_ip: Optional[str] = None,
    after_lift_callback: Optional[Callable[[], None]] = None,
) -> bool:
    """对外唯一接口：给定左右末端目标位置 (m)，执行完整抓取序列。

    姿态分量（四元数）使用模块内部默认，与历史 `forward` 目标一致。
    可选 ``robot_ip`` 仅用于灵巧手网络地址，非 target 几何参数。

    Args:
        right_pos: 右手末端位置，长度 3。
        left_pos: 左手末端位置，长度 3；默认 ``(0.001, 0.212, -0.204)``。
        robot_ip: 灵巧手所在机器人 IP。
        after_lift_callback: VLAC 验证小幅抬升成功后调用的可选回调。

    Returns:
        序列是否全部成功。
    """
    wp = _waypoint_from_positions(right_pos, left_pos)
    cfg = _GraspConfig(robot_ip=robot_ip or get_g1_robot_ip())
    return _with_controller(
        cfg,
        lambda c: c.execute_grasp_sequence(
            wp,
            after_lift_callback=after_lift_callback,
        ),
    )


def detect_grasp_target(
    object_name: str,
    *,
    detection_index: int = 0,
    right_target_offset: Optional[Sequence[float]] = None,
) -> dict:
    """通过 YOLO + D435i 深度估计抓取目标点。

    Returns:
        包含 ``target_position``、``right_pos``、``left_pos`` 和 ``detection`` 的 dict。
    """
    from robot_sdk.yolo_sdk import YoloSDK

    if detection_index < 0:
        raise ValueError("detection_index must be >= 0")

    yolo = YoloSDK()
    try:
        yolo.start()
        detections = yolo.segment_3d(object_name)
    finally:
        yolo.stop()

    if not detections:
        raise RuntimeError(f"未检测到可抓取目标: {object_name}")

    detections = sorted(
        detections,
        key=lambda d: float(d.get("confidence", 0.0)),
        reverse=True,
    )
    if detection_index >= len(detections):
        raise IndexError(
            f"detection_index={detection_index} 超出检测数量 {len(detections)}"
        )

    det = detections[detection_index]
    target = np.asarray(det["position_base"], dtype=float).reshape(3)
    if right_target_offset is not None:
        target = target + np.asarray(right_target_offset, dtype=float).reshape(3)

    return {
        "target_position": target.tolist(),
        "right_pos": target.tolist(),
        "left_pos": list(_DEFAULT_LEFT_HOLD_POS),
        "detection": det,
        "detections": detections,
    }


def release_object(*, robot_ip: Optional[str] = None) -> bool:
    """执行松手/回 home 序列。"""
    cfg = _GraspConfig(robot_ip=robot_ip or get_g1_robot_ip())
    return _with_controller(cfg, lambda c: c.execute_release_sequence())


# ---------------------------------------------------------------------------
# detect_grasp_target_anygrasp：调用 AnyGrasp HTTP 服务获取抓取位姿
# ---------------------------------------------------------------------------

def detect_grasp_target_anygrasp(
    object_name: str,
    *,
    detection_index: int = 0,
    grasp_index: int = 0,
    right_target_offset: Optional[Sequence[float]] = None,
) -> dict:
    """调用 AnyGrasp 服务获取抓取位姿，返回格式与 detect_grasp_target 完全一致。

    服务地址优先级: 环境变量 ``GRASPANYTHING_URL`` > ``robot_sdk/config.yaml`` 的 ``graspanything.url`` >
    内置默认 ``http://127.0.0.1:8015/grasp/detect``。
    """
    import base64

    import cv2
    import httpx

    from robot_sdk.yolo_sdk import YoloSDK, _T_DEPTH_TO_TORSO

    def _gcfg() -> dict:
        from robot_sdk.config import get_config as _gc

        return _gc()

    # 取帧
    yolo = YoloSDK()
    try:
        yolo.start()
        cam = yolo._ensure_camera()
        rgb, depth_u16 = cam.get_frame()
        intr = cam.get_intrinsics()
    finally:
        yolo.stop()

    # 编码请求
    _, color_b64 = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    _, depth_b64 = cv2.imencode(".png", np.ascontiguousarray(depth_u16, np.uint16))
    K = [[intr["fx"], 0, intr["cx"]], [0, intr["fy"], intr["cy"]], [0, 0, 1]]

    url = os.environ.get(
        "GRASPANYTHING_URL",
        _gcfg().get("graspanything", {}).get("url", "http://127.0.0.1:8015/grasp/detect"),
    )
    resp = httpx.post(url, json={
        "color_image": base64.b64encode(color_b64).decode(),
        "depth_image": base64.b64encode(depth_b64).decode(),
        "camera_intrinsics": K,
        "object_name": object_name,
        "top_k": max(1, detection_index + 1),
    }, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        raise RuntimeError(f"AnyGrasp 未检测到目标: {object_name}")

    results.sort(key=lambda r: r.get("confidence", 0), reverse=True)
    det = results[min(detection_index, len(results) - 1)]
    grasps = det.get("grasps", [])
    if not grasps:
        raise RuntimeError(f"AnyGrasp 无有效抓取候选")
    grasps.sort(key=lambda g: g.get("score", 0), reverse=True)
    grasp = grasps[min(grasp_index, len(grasps) - 1)]

    # camera → torso 变换
    tx, ty, tz = grasp["translation_camera"]
    pos = _T_DEPTH_TO_TORSO @ np.array([tx, ty, tz, 1])
    pos = pos[:3]
    if right_target_offset is not None:
        pos += np.asarray(right_target_offset)

    # 四元数 camera → torso：只旋转向量部分
    R = _T_DEPTH_TO_TORSO[:3, :3]
    qx, qy, qz, qw = grasp["quaternion_camera_xyzw"]
    rv = R @ np.array([qx, qy, qz])
    right_quat = np.array([rv[0], rv[1], rv[2], qw])

    return {
        "target_position": pos.tolist(),
        "right_pos": pos.tolist(),
        "right_quat": right_quat.tolist(),
        "left_pos": list(_DEFAULT_LEFT_HOLD_POS),
        "detection": det,
        "grasps": grasps,
    }
