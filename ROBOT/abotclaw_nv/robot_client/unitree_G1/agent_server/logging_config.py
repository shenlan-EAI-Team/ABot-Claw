"""Logging configuration for the agent server."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(name: str = "agent_server", level: int = logging.INFO) -> logging.Logger:
    """Setup logging with consistent formatting.

    同时为 ``utils.ik`` 配置独立 handler 与级别：否则子 logger（如 ``utils.ik.g1_ik_sdk``）
    会向上传到 **root**，而 root 默认 WARNING，导致 INFO 记录被丢弃。

    Args:
        name: Logger name
        level: Logging level

    Returns:
        Configured logger
    """
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    def _attach(name_: str) -> logging.Logger:
        lg = logging.getLogger(name_)
        lg.setLevel(level)
        lg.handlers.clear()
        h = logging.StreamHandler(sys.stdout)
        h.setLevel(level)
        h.setFormatter(formatter)
        lg.addHandler(h)
        return lg

    logger = _attach(name)

    # IK / RobotBridge 诊断日志（g1_ik_sdk 等）
    ik = logging.getLogger("utils.ik")
    ik.setLevel(level)
    ik.handlers.clear()
    ik_h = logging.StreamHandler(sys.stdout)
    ik_h.setLevel(level)
    ik_h.setFormatter(formatter)
    ik.addHandler(ik_h)
    ik.propagate = False

    return logger


def get_log_buffer():
    """Get the log buffer (not implemented - returns None)."""
    return None
