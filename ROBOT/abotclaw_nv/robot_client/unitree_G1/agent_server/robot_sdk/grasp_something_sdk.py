"""G1 通用抓取 SDK。

对外仅暴露 ``grasp_something`` 一个接口，封装以下完整流程：

1. YOLO 检测指定目标
2. 保存一张当前检测图到 agent_server/logs
3. 根据检测结果执行抓取
4. 结束后回到 home 位并释放控制权

运行时自动加载项目根目录的 ``config.env`` 以获取网络接口等配置。
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

# 自动加载 config.env（与 start_server.sh 保持一致）
# grasp_something_sdk.py -> robot_sdk/ -> agent_server/ -> unitree_G1/ -> robot_client/ -> ABot-Claw/ (5层)
_CONFIG_ENV = Path(__file__).resolve().parent.parent.parent.parent.parent / "config.env"
if _CONFIG_ENV.exists():
    _result = subprocess.run(
        ["bash", "-c", f"source {_CONFIG_ENV} && env"],
        capture_output=True,
        text=True,
    )
    if _result.returncode == 0:
        for line in _result.stdout.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key, value)

from robot_sdk.g1_grasp_sdk import (
    _Q_I,
    detect_grasp_target,
    detect_grasp_target_anygrasp,
    grasp_target,
)
from robot_sdk.config import get_g1_robot_ip
from robot_sdk.yolo_sdk import YoloSDK

__all__ = ["grasp_something"]


_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _quat_to_euler_deg(quat: Sequence[float]) -> tuple[float, float, float]:
    """将 [x, y, z, w] 形式的四元数转换为 ZYX 欧拉角（翻滚、俯仰、偏航），返回角度制。"""
    x, y, z, w = quat
    # Roll (X)
    sinr_cosp = 2 * (w * x + y * z)
    cosr_cosp = 1 - 2 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))
    # Pitch (Y)
    sinp = 2 * (w * y - z * x)
    if abs(sinp) >= 1:
        pitch = math.copysign(90.0, sinp)
    else:
        pitch = math.degrees(math.asin(sinp))
    # Yaw (Z)
    siny_cosp = 2 * (w * z + x * y)
    cosy_cosp = 1 - 2 * (y * y + z * z)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))
    return (round(roll, 2), round(pitch, 2), round(yaw, 2))


def _format_pos(pos: Sequence[float], quat: Sequence[float]) -> str:
    px, py, pz = [round(v, 4) for v in pos]
    roll, pitch, yaw = _quat_to_euler_deg(quat)
    return (
        f"pos=[{px:.4f}, {py:.4f}, {pz:.4f}] m  "
        f"rpy=[{roll:7.2f}°, {pitch:7.2f}°, {yaw:7.2f}°]"
    )


def _save_detection_image(log_dir: Path, timestamp: int) -> Optional[str]:
    yolo = YoloSDK()
    try:
        yolo.start()
        img_path = log_dir / f"yolo_detection_{timestamp}.jpg"
        return yolo.save_detection_image(str(img_path))
    finally:
        if hasattr(yolo, "stop"):
            yolo.stop()


def grasp_something(
    object_name: str,
    *,
    robot_ip: Optional[str] = None,
    detection_index: int = 0,
    right_target_offset: Optional[Sequence[float]] = None,
    log_dir: Optional[str | Path] = None,
    after_lift_callback: Optional[Callable[[], None]] = None,
) -> bool:
    """检测并抓取指定目标。

    Args:
        object_name: 目标类别名，例如 ``"bottle"``。
        robot_ip: 灵巧手所在机器人 IP。
        detection_index: 多目标时按置信度排序后选取的索引。
        right_target_offset: 可选右手抓取偏移。
        log_dir: 检测图保存目录，默认 ``agent_server/logs``。
        after_lift_callback: ``lift_return`` 成功后、最终回 home 前调用的可选回调。

    Returns:
        完整流程是否成功。
    """
    out_dir = Path(log_dir) if log_dir is not None else _DEFAULT_LOG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())

    print("=== 开始执行 G1 抓取任务 ===")
    print(f"目标物体: {object_name}")

    try:
        print("\n[Step 0] 正在通过 D435i 进行 YOLO 检测...")
        # plan = detect_grasp_target(
        plan = detect_grasp_target_anygrasp(
            object_name,
            detection_index=detection_index,
            right_target_offset=right_target_offset,
        )

        saved_img = _save_detection_image(out_dir, timestamp)
        if saved_img:
            print(f"✅ YOLO 检测图像已保存: {saved_img}")
        print(
            "   检测到目标坐标 (Torso): "
            f"X={plan['target_position'][0]:.3f}, "
            f"Y={plan['target_position'][1]:.3f}, "
            f"Z={plan['target_position'][2]:.3f}"
        )
        # rq = plan.get("right_quat", _Q_I)
        print(f"   右手  {_format_pos(plan['right_pos'], plan.get('right_quat', _Q_I))}")
        # print(f"   右手  {_format_pos(plan['right_pos'], rq)}")
        # print(f"   左手  {_format_pos(plan['left_pos'], lq)}")
        print("\n[Step 1-7] 正在执行机械臂抓取序列...")
        success = grasp_target(
            plan["right_pos"],
            plan.get("left_pos", [0.01, 0.212, -0.204 ]),
            robot_ip=robot_ip or get_g1_robot_ip(),
            after_lift_callback=after_lift_callback,
        )

        if success:
            print("✅ 抓取序列执行成功！")
        else:
            print("❌ 抓取序列执行失败。")
        return success
    except Exception as e:
        print(f"❌ 任务执行出错: {e}")
        return False


def _parse_offset(raw: Optional[str]) -> Optional[tuple[float, float, float]]:
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("offset must be formatted as x,y,z")
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("offset values must be numbers") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Direct G1 grasp helper")
    parser.add_argument(
        "object_name",
        nargs="?",
        default="bottle",
        help="YOLO class name to grasp, default: bottle",
    )
    parser.add_argument("--robot-ip", default=None, help="G1 hand server IP; default reads config.yaml/G1_ROBOT_IP")
    parser.add_argument("--detection-index", type=int, default=0, help="Detection index after sorting by confidence")
    parser.add_argument("--right-offset", type=_parse_offset, default=None, help="Right hand target offset in meters, x,y,z")
    parser.add_argument("--log-dir", default=None, help="Directory to save detection images")
    args = parser.parse_args(argv)

    ok = grasp_something(
        args.object_name,
        robot_ip=args.robot_ip,
        detection_index=args.detection_index,
        right_target_offset=args.right_offset,
        log_dir=args.log_dir,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
# python -m robot_sdk.grasp_something_sdk bottle
