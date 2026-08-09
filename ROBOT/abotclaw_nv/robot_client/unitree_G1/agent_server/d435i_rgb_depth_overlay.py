#!/usr/bin/env python3
"""
使用 ``g1_d435i_camera.G1D435iCamera`` 拉取 RGB 与 Z16 深度，将深度伪彩色与彩色图叠加显示。

运行（任选其一）::

    cd robot_client/unitree_G1/agent_server/robot_sdk && python d435i_rgb_depth_overlay.py

    python /path/to/d435i_rgb_depth_overlay.py --host 192.168.123.164 --port 8765

环境变量 ``G1_ROBOT_IP`` 或 ``D435I_HOST`` 可在未传 ``--host`` 时作为默认 IP。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

_SDK_DIR = Path(__file__).resolve().parent
if str(_SDK_DIR) not in sys.path:
    sys.path.insert(0, str(_SDK_DIR))

from g1_d435i_camera import G1D435iCamera, _depth_to_bgr_vis


def blend_rgb_depth(rgb: np.ndarray, depth: np.ndarray, rgb_weight: float) -> np.ndarray:
    """rgb: HWC RGB uint8；depth: HW uint16。返回 BGR 叠加图。"""
    rgb_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    depth_bgr = _depth_to_bgr_vis(depth)
    h, w = rgb_bgr.shape[:2]
    if depth_bgr.shape[:2] != (h, w):
        depth_bgr = cv2.resize(depth_bgr, (w, h), interpolation=cv2.INTER_NEAREST)
    w_rgb = float(np.clip(rgb_weight, 0.0, 1.0))
    w_d = 1.0 - w_rgb
    return cv2.addWeighted(rgb_bgr, w_rgb, depth_bgr, w_d, 0.0)


def main() -> None:
    default_host = os.environ.get("G1_D435I_HOST", G1D435iCamera.DEFAULT_HOST)
    p = argparse.ArgumentParser(description="D435i TCP：RGB + 深度伪彩色叠加显示")
    p.add_argument("--host", default=default_host, help="相机服务 IP")
    p.add_argument("--port", type=int, default=G1D435iCamera.DEFAULT_PORT, help="TCP 端口（同 main.py --stream_port）")
    p.add_argument(
        "--rgb-weight",
        type=float,
        default=0.55,
        help="叠加时 RGB 权重，深度为 (1-该值)。约 0.5~0.65 较易辨认",
    )
    p.add_argument(
        "--shared",
        action="store_true",
        help="使用进程内单例（默认独立 viewer 连接，传此则与其它模块共用同一 G1D435iCamera 实例）",
    )
    args = p.parse_args()

    cam = G1D435iCamera(host=args.host, port=args.port, enable_depth=True, shared=args.shared)
    if not cam.initialize():
        print("连接失败，检查推流与 G1_D435I_HOST / 端口")
        sys.exit(1)

    print("叠加窗口：按 q 退出；按 s 保存当前叠加图与 raw 深度")
    frame_count = 0
    last_t = time.perf_counter()

    try:
        while True:
            rgb, depth = cam.get_frame()
            if rgb is None:
                cv2.imshow("D435i RGB+Depth overlay", np.zeros((240, 320, 3), dtype=np.uint8))
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                continue

            if depth is not None:
                vis = blend_rgb_depth(rgb, depth, args.rgb_weight)
                label = "overlay"
            else:
                vis = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                label = "rgb_only"

            now = time.perf_counter()
            dt = now - last_t
            last_t = now
            fps = 1.0 / dt if dt > 1e-6 else 0.0
            cv2.putText(
                vis,
                f"{label}  FPS:{fps:.1f}",
                (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow("D435i RGB+Depth overlay", vis)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s") and depth is not None:
                frame_count += 1
                base = f"d435i_overlay_{frame_count}"
                cv2.imwrite(f"{base}.png", vis)
                cv2.imwrite(f"{base}_rgb.png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                np.save(f"{base}_depth_u16.npy", depth)
                print(f"已写入 {base}.png / {base}_rgb.png / {base}_depth_u16.npy")
    finally:
        cam.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
