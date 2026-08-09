"""G1 Robot SDK configuration — 与 arm_piper 的 robot_sdk/config.py 对齐。

从本目录下 config.yaml 读取运行参数；可通过环境变量 ROBOT_SDK_CONFIG 指定其它路径。
模块级 G1_* 常量仍为 Python 默认值，供代码直接引用。

yaml 中的 ${ENV_VAR} 占位符会在加载时自动替换为同名环境变量值，
从而实现与 config.env 的完全对齐（yaml 中无需硬编码 IP）。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict

import yaml

# G1 specific constants（与 yaml 解耦，供类型与逻辑默认值）
G1_DEFAULT_NETWORK_INTERFACE = os.environ.get(
    "G1_NETWORK_INTERFACE",
    os.environ.get("G1_ARM_NETWORK_IFACE", os.environ.get("UNITREE_IFACE", "eth0")),
)
G1_DEFAULT_SPORT_TIMEOUT = 10.0
G1_DEFAULT_ARM_TIMEOUT = 10.0

G1_ARM_JOINT_LIMITS = {
    "left_shoulder_pitch": (-2.5, 2.5),
    "left_shoulder_roll": (-1.5, 1.5),
    "left_shoulder_yaw": (-2.5, 2.5),
    "left_elbow_pitch": (-2.5, 0.0),
    "right_shoulder_pitch": (-2.5, 2.5),
    "right_shoulder_roll": (-1.5, 1.5),
    "right_shoulder_yaw": (-2.5, 2.5),
    "right_elbow_pitch": (-2.5, 0.0),
}

G1_BODY_HEIGHT_MIN = 0.5
G1_BODY_HEIGHT_MAX = 0.8

G1_MAX_VELOCITY_X = 1.0
G1_MAX_VELOCITY_Y = 0.5
G1_MAX_VELOCITY_YAW = 1.0

def _default_robot_ip() -> str:
    return os.environ.get(
        "G1_ROBOT_IP",
        os.environ.get("UNITREE_ROBOT_IP", os.environ.get("ROBOT_IP", "192.168.123.164")),
    )


G1_DEFAULT_ROBOT_IP = _default_robot_ip()

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

_cached_config: Dict[str, Any] | None = None

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


_re_env = re.compile(r"\$\{([^}]+)\}")


def _interpolate(value: Any) -> Any:
    """递归展开 yaml 中的 ${ENV_VAR} 占位符（支持嵌套 dict/list/str）。"""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            return os.environ.get(m.group(1), m.group(0))
        return _re_env.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(item) for item in value]
    return value


def get_config(config_path: str | None = None) -> Dict[str, Any]:
    """加载并缓存 yaml。优先级: 参数 > ROBOT_SDK_CONFIG > robot_sdk/config.yaml。"""
    global _cached_config

    if config_path is None and _cached_config is not None:
        return _cached_config

    path = config_path or os.environ.get("ROBOT_SDK_CONFIG", _DEFAULT_CONFIG_PATH)
    cfg: Dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                cfg = loaded if isinstance(loaded, dict) else {}
        except Exception:
            cfg = {}

    if config_path is None:
        _cached_config = _interpolate(cfg)
    return _cached_config


def reload_config(config_path: str | None = None) -> Dict[str, Any]:
    """强制重新加载（清除缓存）。"""
    global _cached_config
    _cached_config = None
    return get_config(config_path)


def get_g1_robot_ip(default: str = G1_DEFAULT_ROBOT_IP) -> str:
    """G1 主机 IP，优先级：环境变量 > config.yaml > 内置默认。"""
    for name in ("G1_ROBOT_IP", "UNITREE_ROBOT_IP", "ROBOT_IP"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    cfg = get_config() or {}
    g1 = cfg.get("g1") or {}
    value = g1.get("robot_ip")
    return str(value or default)


def get_g1_d435i_host(default: str | None = None) -> str:
    """D435i TCP 推流主机，默认跟随 G1 主机 IP。"""
    value = os.environ.get("G1_D435I_HOST")
    if value and value.strip():
        return value.strip()
    cfg = get_config() or {}
    g1 = cfg.get("g1") or {}
    value = g1.get("d435i_host")
    if value:
        return str(value)
    return default or get_g1_robot_ip()


def get_g1_d435i_port(default: int = 8765) -> int:
    """D435i TCP 推流端口，默认 8765。"""
    value = os.environ.get("G1_D435I_PORT")
    if value and value.strip():
        try:
            return int(value)
        except ValueError:
            pass
    cfg = get_config() or {}
    g1 = cfg.get("g1") or {}
    try:
        return int(g1.get("d435i_port", default))
    except (TypeError, ValueError):
        return int(default)
