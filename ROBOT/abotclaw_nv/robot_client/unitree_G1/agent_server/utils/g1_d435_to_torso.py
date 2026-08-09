"""D435 深度相机系到 G1 torso 的刚体变换（与项目内标定/URDF 约定一致）。

针孔反投影得到的 ``(x,y,z)`` 视为深度相机坐标系下的点，经 ``R_DEPTH_TO_URDF`` 与
D435 在 torso 下的位姿，变换到 torso。
"""

from __future__ import annotations

import json
import math
import os
from typing import Optional

import numpy as np

# 深度相机坐标系 → URDF 中 d435_link 常用轴向约定
R_DEPTH_TO_URDF = np.array(
    [
        [0, 0, 1],
        [-1, 0, 0],
        [0, -1, 0],
    ],
    dtype=np.float64,
)

D435_POSITION_IN_TORSO = np.array([0.0576235, 0.01753, 0.42987], dtype=np.float64)
D435_ORIENTATION_RPY = np.array([0.0, 0.8307767239493009, 0.0], dtype=np.float64)


def _rpy_to_rot(r: float, p: float, y: float) -> np.ndarray:
    """RPY (Roll-Pitch-Yaw) → 旋转矩阵 (ZYX 顺序)。"""
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ],
        dtype=np.float64,
    )


class D435ToTorsoTransformer:
    """D435 深度相机坐标 → G1 torso 坐标。"""

    def __init__(self, calibration_path: Optional[str] = None, use_compensation: bool = False):
        if calibration_path and os.path.exists(calibration_path):
            with open(calibration_path, "r", encoding="utf-8") as f:
                cal_data = json.load(f)
            self.K = np.asarray(cal_data["K_color_1280x720"], dtype=np.float64)
            self.use_aligned_depth = True
            self.K_color = np.asarray(cal_data["K_color"], dtype=np.float64)
            self.color_width = int(cal_data["color_image_size"][0])
            self.color_height = int(cal_data["color_image_size"][1])
        else:
            self.K = np.array(
                [
                    [650.0841064453125, 0.0, 644.9938354492188],
                    [0.0, 650.0841064453125, 358.162353515625],
                    [0.0, 0.0, 1.0],
                ],
                dtype=np.float64,
            )
            self.use_aligned_depth = False
            self.K_color = np.array(
                [
                    [1361.83, 0, 976.25],
                    [0, 1361.47, 564.91],
                    [0, 0, 1],
                ],
                dtype=np.float64,
            )
            self.color_width = 1920
            self.color_height = 1080

        position = D435_POSITION_IN_TORSO.copy()
        rpy = D435_ORIENTATION_RPY.copy()

        R_torso_from_d435_link = _rpy_to_rot(float(rpy[0]), float(rpy[1]), float(rpy[2]))
        T_d435_link_to_torso = np.eye(4, dtype=np.float64)
        T_d435_link_to_torso[:3, :3] = R_torso_from_d435_link
        T_d435_link_to_torso[:3, 3] = position

        T_depth_to_d435_link = np.eye(4, dtype=np.float64)
        T_depth_to_d435_link[:3, :3] = R_DEPTH_TO_URDF

        self._T_depth_to_torso = T_d435_link_to_torso @ T_depth_to_d435_link

        self.use_compensation = use_compensation
        self.position_compensation = np.array([0.0209, 0.0046, 0.0791], dtype=np.float64)

    def camera_to_torso_coords(self, points_cam: np.ndarray) -> np.ndarray:
        """将深度相机坐标系下的 3D 点变换到 torso。"""
        points_cam = np.asarray(points_cam, dtype=np.float64)

        if points_cam.ndim == 1:
            points_cam = points_cam.reshape(1, -1)
            single_input = True
        else:
            single_input = False

        points_hom = np.concatenate([points_cam, np.ones((points_cam.shape[0], 1))], axis=1).T
        points_torso_hom = self._T_depth_to_torso @ points_hom
        points_torso = points_torso_hom[:3, :].T

        if self.use_compensation:
            points_torso = points_torso + self.position_compensation.reshape(1, 3)

        if single_input:
            points_torso = points_torso.squeeze(axis=0)

        return points_torso

    def pixel_to_torso_coords(
        self, pixel_x: float, pixel_y: float, depth_m: float
    ) -> np.ndarray:
        """从像素坐标 + 深度直接转换到 torso 坐标。

        Args:
            pixel_x: 像素 x 坐标
            pixel_y: 像素 y 坐标
            depth_m: 深度值（米）

        Returns:
            torso 坐标系下的 [x, y, z]（米）
        """
        # 针孔反投影：像素 → 相机坐标系
        fx, fy = self.K[0, 0], self.K[1, 1]
        cx, cy = self.K[0, 2], self.K[1, 2]

        x_cam = (pixel_x - cx) * depth_m / fx
        y_cam = (pixel_y - cy) * depth_m / fy
        z_cam = depth_m

        # 相机坐标系 → torso
        pos_camera = np.array([x_cam, y_cam, z_cam])
        return self.camera_to_torso_coords(pos_camera)
