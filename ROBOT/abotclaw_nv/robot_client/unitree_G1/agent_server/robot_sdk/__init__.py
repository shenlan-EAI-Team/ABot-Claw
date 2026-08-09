"""G1 Robot SDK — 与 arm_piper 的 ``robot_sdk`` 契约对齐（文档 + 懒加载导出）。

``/code/execute`` 子进程由 ``robot_sdk.code_execute_bootstrap`` 注入 ``G1RobotEnv`` 实例为 ``env``，
并保留 ``camera``、``camera_d435i``、``yolo``、``grasp_target``、``memory``、``face``、``tts`` 及可调用懒代理 ``Nav2Anywhere`` 等兼容别名。
网络与 HTTP 默认值来自本目录 ``config.yaml``，环境变量 ``ROBOT_SDK_CONFIG`` 可覆盖路径（与 Piper 一致）。

包内实现为平铺模块；顶层 ``from robot_sdk import FaceSDK`` 等使用 ``__getattr__`` 懒加载，
以便 ``from robot_sdk.config import get_config`` 仅加载轻量配置模块，不触发相机 / ROS2 等重依赖。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "G1D455Camera",
    "G1D435iCamera",
    "G1RobotEnv",
    "grasp_target",
    "HandClient",
    "send_hand_command",
    "HAND_COMMANDS",
    "Nav2Anywhere",
    "TTSClient",
    "tts_speak",
    "FaceSDK",
    "MemorySDK",
    "Pose",
    "AdjustPosiSDK",
    "G1_DEFAULT_NETWORK_INTERFACE",
    "G1_DEFAULT_SPORT_TIMEOUT",
    "G1_DEFAULT_ARM_TIMEOUT",
    "G1_ARM_JOINT_LIMITS",
    "G1_BODY_HEIGHT_MIN",
    "G1_BODY_HEIGHT_MAX",
    "G1_MAX_VELOCITY_X",
    "G1_MAX_VELOCITY_Y",
    "G1_MAX_VELOCITY_YAW",
]

_CONFIG_EXPORT_NAMES = frozenset(
    {
        "G1_DEFAULT_NETWORK_INTERFACE",
        "G1_DEFAULT_SPORT_TIMEOUT",
        "G1_DEFAULT_ARM_TIMEOUT",
        "G1_ARM_JOINT_LIMITS",
        "G1_BODY_HEIGHT_MIN",
        "G1_BODY_HEIGHT_MAX",
        "G1_MAX_VELOCITY_X",
        "G1_MAX_VELOCITY_Y",
        "G1_MAX_VELOCITY_YAW",
    }
)

_nav2_cls: Any = None


def _nav2_class() -> Any:
    global _nav2_cls
    if _nav2_cls is None:
        from .navigation_sdk import Nav2Anywhere as _Cls

        _nav2_cls = _Cls
    return _nav2_cls


class _LazyNav2Anywhere:
    """延迟导入 ROS1 ``Nav2Anywhere``（基于 move_base actionlib）。"""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return _nav2_class()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(_nav2_class(), name)


Nav2Anywhere = _LazyNav2Anywhere()


def __getattr__(name: str) -> Any:
    if name in _CONFIG_EXPORT_NAMES:
        from . import config as _cfg

        return getattr(_cfg, name)
    if name == "G1D455Camera":
        from .g1_d455_camera import G1D455Camera

        return G1D455Camera
    if name == "G1D435iCamera":
        from .g1_d435i_camera import G1D435iCamera

        return G1D435iCamera
    if name == "G1RobotEnv":
        from .g1_robot_env import G1RobotEnv

        return G1RobotEnv
    if name == "grasp_target":
        from .g1_grasp_sdk import grasp_target

        return grasp_target
    if name == "HandClient":
        from .hand_sdk import HandClient

        return HandClient
    if name == "send_hand_command":
        from .hand_sdk import send_hand_command

        return send_hand_command
    if name == "HAND_COMMANDS":
        from .hand_sdk import COMMANDS as HAND_COMMANDS

        return HAND_COMMANDS
    if name == "TTSClient":
        from .tts_sdk import TTSClient

        return TTSClient
    if name == "tts_speak":
        from .tts_sdk import tts_speak

        return tts_speak
    if name == "FaceSDK":
        from .face_sdk import FaceSDK

        return FaceSDK
    if name == "MemorySDK":
        from .memory_sdk import MemorySDK

        return MemorySDK
    if name == "Pose":
        from .memory_sdk import Pose

        return Pose
    if name == "AdjustPosiSDK":
        from .adjust_posi_sdk import AdjustPosiSDK

        return AdjustPosiSDK
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
