"""YOLO SDK for Unitree G1 - remote HTTP detect + D435i TCP RGB-D (no ROS).

与 ``arm_piper/.../yolo_sdk.py`` 思路一致:对彩色图调 YOLO HTTP API 取 bbox,
用 D435i 深度图 + 内参做针孔反投影。``position_camera`` 为相机系(米);``position_base``
默认由 ``g1_d435_to_torso.D435ToTorsoTransformer`` 将深度相机系点变换到 G1 torso(刚体 SE(3))。
若需恢复旧行为(仅平移),设 ``YOLO_G1_USE_LEGACY_TORSO_OFFSET=1`` 或使用构造参数。

环境变量与配置(可选)::
    YOLO_URL          检测服务 URL;缺省时读 ``robot_sdk/config.yaml`` 的 ``yolo.url``,再默认 8013
    G1_D435I_HOST     相机 TCP IP;缺省时读 ``g1.robot_ip``(``ROBOT_SDK_CONFIG`` 可改 yaml 路径)
    G1_D435I_PORT     相机 TCP 端口;缺省时读 ``g1.d435i_port``
    YOLO_G1_CALIB_JSON  可选;存在时传入 ``D435ToTorsoTransformer`` 加载 K 等标定
    YOLO_G1_USE_LEGACY_TORSO_OFFSET  设为 1 时仅用 ``torso_offset_m`` / ``YOLO_G1_TORSO_OFFSET_*`` 平移,不做 SE(3)
"""

from __future__ import annotations

import base64
import io
import logging
import os
import random
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore


# ============================================================================
# D435 深度相机坐标系 → G1 Torso 坐标系 转换(内嵌)
# ============================================================================

import math

# 深度相机坐标系 → URDF 中 d435_link 常用轴向约定
_R_DEPTH_TO_URDF = np.array(
    [
        [0, 0, 1],
        [-1, 0, 0],
        [0, -1, 0],
    ],
    dtype=np.float64,
)

# D435 在 Torso 下的位姿
_D435_POSITION_IN_TORSO = np.array([0.0576235, 0.01753, 0.42987], dtype=np.float64)
_D435_ORIENTATION_RPY = np.array([0.0, 0.8307767239493009, 0.0], dtype=np.float64)


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


def _build_torso_transform() -> np.ndarray:
    """构建 D435 深度相机 → Torso 的 4x4 变换矩阵。"""
    position = _D435_POSITION_IN_TORSO.copy()
    rpy = _D435_ORIENTATION_RPY.copy()

    R_torso_from_d435_link = _rpy_to_rot(float(rpy[0]), float(rpy[1]), float(rpy[2]))
    T_d435_link_to_torso = np.eye(4, dtype=np.float64)
    T_d435_link_to_torso[:3, :3] = R_torso_from_d435_link
    T_d435_link_to_torso[:3, 3] = position

    T_depth_to_d435_link = np.eye(4, dtype=np.float64)
    T_depth_to_d435_link[:3, :3] = _R_DEPTH_TO_URDF

    return T_d435_link_to_torso @ T_depth_to_d435_link


# 预计算变换矩阵(模块加载时计算一次)
_T_DEPTH_TO_TORSO = _build_torso_transform()


def _camera_coords_to_torso(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """将 D435 相机坐标系下的点转换到 Torso 坐标系(私有函数)。"""
    p = np.array([x, y, z, 1.0], dtype=np.float64)
    p_torso = _T_DEPTH_TO_TORSO @ p
    return (float(p_torso[0]), float(p_torso[1]), float(p_torso[2]))


# ============================================================================


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _g1_d435i_tcp_defaults() -> Tuple[str, int]:
    """D435i TCP: 环境变量 G1_ROBOT_IP / G1_D435I_HOST / G1_D435I_PORT。"""
    try:
        from .config import get_g1_d435i_host, get_g1_d435i_port
        return get_g1_d435i_host(), get_g1_d435i_port()
    except Exception:
        host = os.environ.get("G1_ROBOT_IP") or os.environ.get("G1_D435I_HOST") or "192.168.123.164"
        port = int(os.environ.get("G1_D435I_PORT") or 8765)
        return host, port


class YoloSDK:
    """YOLO 检测：HTTP API；可选 G1 D435i（``G1D435iCamera`` TCP 流）。
    
    仅用 HTTP、不连相机时：``YoloSDK(require_camera=False).start()`` 后调 ``detect_on_rgb(rgb)``，
    或不经 ``start()`` 直接 ``detect_on_rgb``（不依赖 D435i）。
    
    **使用示例**::
    
        from robot_sdk.yolo_sdk import YoloSDK
        
        # 使用 D435i 相机（默认）
        yolo = YoloSDK()
        yolo.start()
        
        # 检测环境中的物体
        labels = yolo.detect_env()
        print("检测到的物体:", labels)
        
        # 获取某个物体的 3D 位置
        detections = yolo.segment_3d("bottle")
        for d in detections:
            print(f"{d['label']}: base={d['position_base']}, depth={d['depth_m']:.3f}m")
        
        # 保存带框的检测图
        path = yolo.save_detection_image()
        print("已保存:", path)
        
        # 仅用 HTTP（不连相机）
        yolo_http = YoloSDK(require_camera=False)
        rgb = ...  # 你的 RGB 图像 (H,W,3) uint8
        detections = yolo_http.detect_on_rgb(rgb)
    """

    def __init__(
        self,
        yolo_url: Optional[str] = None,
        *,
        require_camera: bool = True,
        camera_host: Optional[str] = None,
        camera_port: Optional[int] = None,
        camera_timeout: float = 5.0,
        conf_thres: float = 0.5,
        iou_thres: float = 0.45,
        request_timeout: float = 10.0,
        depth_scale_m: float = 0.001,
        torso_offset_m: Optional[Tuple[float, float, float]] = None,
        use_torso_transform: Optional[bool] = None,
        calibration_path: Optional[str] = None,
        camera: Any = None,
    ) -> None:
        """
        Args:
            yolo_url: YOLO POST JSON ``{\"image\": base64_png, ...}``,默认读 ``YOLO_URL`` 或 8013
            require_camera: 为 False 时 ``start()`` 不初始化 D435i;``detect_env``/``segment_3d`` 等仍依赖相机的接口不可用,请用 ``detect_on_rgb``。
            camera_host / camera_port: D435i TCP(与 ``main.py --stream_port`` 一致)
            depth_scale_m: 深度 uint16 乘以该系数得到米(默认 0.001,即毫米)
            torso_offset_m: 旧模式(``use_torso_transform=False`` 或环境 ``YOLO_G1_USE_LEGACY_TORSO_OFFSET=1``)下,
                将相机系点平移到 torso 的偏移 (x,y,z) 米;刚体变换模式下通常保持 (0,0,0)。
            use_torso_transform: 是否使用 ``D435ToTorsoTransformer``;默认 True,除非环境 ``YOLO_G1_USE_LEGACY_TORSO_OFFSET=1``。
            calibration_path: 相机标定 JSON 路径;默认读 ``YOLO_G1_CALIB_JSON``。
            camera: 可注入已初始化的 ``G1D435iCamera``(测试用);默认内部创建
        """
        self._require_camera = bool(require_camera)
        _yaml_host, _yaml_port = _g1_d435i_tcp_defaults()
        try:
            from .config import get_config as _gc

            _ycfg = (_gc() or {}).get("yolo") or {}
        except Exception:
            _ycfg = {}
        self._yolo_url = yolo_url or os.environ.get("YOLO_URL") or _ycfg.get("url", "http://127.0.0.1:8013/detect")
        self._camera_host = camera_host or os.environ.get("G1_D435I_HOST") or _yaml_host
        if camera_port is not None:
            self._camera_port = int(camera_port)
        else:
            env_port = os.environ.get("G1_D435I_PORT")
            if env_port is not None and str(env_port).strip() != "":
                self._camera_port = int(env_port)
            else:
                self._camera_port = int(_yaml_port)
        self._camera_timeout = float(camera_timeout)
        self._conf_thres = float(conf_thres)
        self._iou_thres = float(iou_thres)
        self._request_timeout = float(request_timeout)
        self._depth_scale_m = float(depth_scale_m)
        off = torso_offset_m
        if off is None:
            ox = _env_float("YOLO_G1_TORSO_OFFSET_X", 0.0)
            oy = _env_float("YOLO_G1_TORSO_OFFSET_Y", 0.0)
            oz = _env_float("YOLO_G1_TORSO_OFFSET_Z", 0.0)
            self._torso_offset = np.array([ox, oy, oz], dtype=np.float64)
        else:
            self._torso_offset = np.array(off, dtype=np.float64)

        if use_torso_transform is None:
            self._use_torso_transform = not _env_bool(
                "YOLO_G1_USE_LEGACY_TORSO_OFFSET", False
            )
        else:
            self._use_torso_transform = bool(use_torso_transform)

        calib = calibration_path or os.environ.get("YOLO_G1_CALIB_JSON")
        calib = calib if (calib and os.path.isfile(os.path.expanduser(calib))) else None
        # 注:坐标转换已内嵌,不再依赖 utils.g1_d435_to_torso

        self._external_camera = camera
        self._camera: Any = None
        self._started = False
        # detect_env 写入,segment_3d 优先消费,保证「先 detect_env 再 segment_3d」共用同一帧与 YOLO 结果
        self._cached_rgb: Optional[np.ndarray] = None
        self._cached_depth_u16: Optional[np.ndarray] = None
        self._cached_detections: Optional[List[Dict[str, Any]]] = None

    def _clear_perception_cache(self) -> None:
        self._cached_rgb = None
        self._cached_depth_u16 = None
        self._cached_detections = None

    def _ensure_camera(self) -> Any:
        if not self._require_camera:
            raise RuntimeError(
                "当前 YoloSDK 使用 require_camera=False,无 D435i;请使用 detect_on_rgb(rgb) 做纯 HTTP 检测。"
            )
        if self._camera is not None:
            return self._camera
        if self._external_camera is not None:
            self._camera = self._external_camera
            return self._camera
        try:
            from .g1_d435i_camera import G1D435iCamera
        except ImportError:
            from g1_d435i_camera import G1D435iCamera

        self._camera = G1D435iCamera(
            host=self._camera_host,
            port=self._camera_port,
            timeout=self._camera_timeout,
            enable_depth=True,
        )
        return self._camera

    def start(self) -> "YoloSDK":
        if self._started:
            return self
        if httpx is None:
            raise RuntimeError("YoloSDK requires 'httpx' (pip install httpx)")
        if not self._require_camera:
            self._started = True
            logger.info("YoloSDK: started (HTTP only, require_camera=False)")
            return self
        cam = self._ensure_camera()
        if not getattr(cam, "_initialized", False):
            if not cam.initialize():
                raise RuntimeError(
                    f"D435i 相机连接失败 {self._camera_host}:{self._camera_port}。"
                    " 若推流已开:检查是否连错 IP(config.yaml 的 g1.d435i_host,默认用 robot_ip)、"
                    "端口是否与推流一致,以及推流是否监听 0.0.0.0(勿仅 127.0.0.1)。"
                )
        rgb, depth = cam.get_frame()
        if rgb is None:
            raise RuntimeError("D435i 取流失败:首帧 RGB 为空")
        self._started = True
        logger.info("YoloSDK: started (HTTP + G1 D435i)")
        return self

    def stop(self) -> None:
        if not self._started:
            return
        if self._camera is not None and self._external_camera is None:
            try:
                self._camera.close()
            except Exception as e:
                logger.warning("YoloSDK: camera close: %s", e)
            self._camera = None
        self._started = False
        self._clear_perception_cache()
        logger.info("YoloSDK: stopped")

    def __enter__(self) -> "YoloSDK":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 几何与 HTTP
    # ------------------------------------------------------------------ #

    def _build_K(self, w: int, h: int) -> np.ndarray:
        cam = self._ensure_camera()
        intr = cam.get_intrinsics()
        w0 = max(float(intr.get("width", w)), 1.0)
        h0 = max(float(intr.get("height", h)), 1.0)
        fx = float(intr.get("fx", 386.0)) * (w / w0)
        fy = float(intr.get("fy", 386.0)) * (h / h0)
        cx = float(intr.get("cx", w / 2.0)) * (w / w0)
        cy = float(intr.get("cy", h / 2.0)) * (h / h0)
        return np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float64)

    @staticmethod
    def _encode_image_rgb_png_base64(rgb: np.ndarray) -> str:
        """将 RGB uint8 编为 PNG base64。

        YOLO HTTP 服务(如 ``Services/G1_Yolo``)使用 ``PIL.Image.open(...).convert("RGB")`` 解码,
        若使用 OpenCV ``imencode``(BGR)会导致 R/B 通道对调,检测结果异常。
        """
        if rgb.dtype != np.uint8:
            rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
        im = Image.fromarray(rgb, mode="RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _scale_detections_to_rgb_shape(
        detections: List[Dict[str, Any]],
        rgb_h: int,
        rgb_w: int,
        api_h: Optional[int],
        api_w: Optional[int],
    ) -> List[Dict[str, Any]]:
        """若服务端返回的解码尺寸与本地 RGB 不一致,将 xyxy 线性映射到本地分辨率。"""
        if not detections or not api_h or not api_w:
            return detections
        if api_h == rgb_h and api_w == rgb_w:
            return detections
        sx = rgb_w / float(api_w)
        sy = rgb_h / float(api_h)
        out: List[Dict[str, Any]] = []
        for d in detections:
            dd = dict(d)
            for k in ("x1", "x2"):
                if k in dd:
                    dd[k] = float(dd[k]) * sx
            for k in ("y1", "y2"):
                if k in dd:
                    dd[k] = float(dd[k]) * sy
            out.append(dd)
        return out

    def _get_frame_detections(self) -> Tuple[np.ndarray, Optional[np.ndarray], List[Dict[str, Any]]]:
        """抓取一帧并调用 YOLO 一次(RGB + 深度 + detections)。"""
        cam = self._ensure_camera()
        rgb, depth_u16 = cam.get_frame()
        if rgb is None:
            raise RuntimeError("D435i RGB 为空")
        img_b64 = self._encode_image_rgb_png_base64(rgb)
        rh, rw = int(rgb.shape[0]), int(rgb.shape[1])
        detections = self._call_yolo_api(img_b64, rgb_h=rh, rgb_w=rw)
        return rgb, depth_u16, detections

    def _call_yolo_api(
        self, img_b64: str, *, rgb_h: int, rgb_w: int
    ) -> List[Dict[str, Any]]:
        assert httpx is not None
        payload = {
            "image": img_b64,
            "conf_thres": self._conf_thres,
            "iou_thres": self._iou_thres,
        }
        # trust_env=False: 不读取 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 环境变量，
        # 避免 socks:// 代理（Clash 等工具常见）与 httpx 不兼容导致崩溃。
        with httpx.Client(trust_env=False, timeout=self._request_timeout) as client:
            r = client.post(self._yolo_url, json=payload)
        r.raise_for_status()
        data = r.json()
        dets = data.get("detections", [])
        api_w = data.get("image_width")
        api_h = data.get("image_height")
        try:
            aw = int(api_w) if api_w is not None else None
            ah = int(api_h) if api_h is not None else None
        except (TypeError, ValueError):
            aw, ah = None, None
        return self._scale_detections_to_rgb_shape(dets, rgb_h, rgb_w, ah, aw)

    def detect_on_rgb(self, rgb: np.ndarray) -> List[Dict[str, Any]]:
        """对已有 RGB 图像调用 YOLO HTTP API,**不经过 D435i**。

        ``rgb`` 为 ``(H,W,3)`` ``uint8``,通道顺序 **RGB**。无需 ``start()`` 与相机。

        Returns:
            与 ``_call_yolo_api`` 相同结构的 ``detections`` 列表(``x1,y1,x2,y2,class_name,...``)。
        """
        if httpx is None:
            raise RuntimeError("YoloSDK requires 'httpx' (pip install httpx)")
        if rgb is None or not isinstance(rgb, np.ndarray) or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("rgb 须为 numpy (H,W,3) uint8 RGB")
        img_b64 = self._encode_image_rgb_png_base64(rgb)
        rh, rw = int(rgb.shape[0]), int(rgb.shape[1])
        return self._call_yolo_api(img_b64, rgb_h=rh, rgb_w=rw)

    @staticmethod
    def _sample_depth_m(
        depth_m: np.ndarray,
        xyxy: Tuple[int, int, int, int],
        num_samples: int = 24,
        return_points: bool = False,
    ) -> Any:
        x1, y1, x2, y2 = xyxy
        cx = (x1 + x2) // 2
        # 采样中心:下边界上方 1/4 处(更接近物体底部/抓取点)
        cy = y2 - (y2 - y1) // 4
        half_range = max(1, min(abs(x2 - x1), abs(y2 - y1)) // 4)
        h, w = depth_m.shape[:2]
        valid: List[float] = []
        valid_points: List[Tuple[int, int]] = []
        # 均匀采样:在窗口内按网格取样,避免随机抖动导致同帧结果不稳定
        grid_n = max(1, int(np.ceil(np.sqrt(max(1, num_samples)))))
        xs = np.linspace(cx - half_range, cx + half_range, num=grid_n)
        ys = np.linspace(cy - half_range, cy + half_range, num=grid_n)
        sampled = 0
        for py_f in ys:
            for px_f in xs:
                if sampled >= num_samples:
                    break
                px = int(round(px_f))
                py = int(round(py_f))
                sampled += 1
                if 0 <= px < w and 0 <= py < h:
                    d = float(depth_m[py, px])
                    if d > 0:
                        valid.append(d)
                        valid_points.append((px, py))
            if sampled >= num_samples:
                break
        if not valid:
            if return_points:
                return None, valid_points
            return None
        valid.sort()
        n = len(valid)
        trimmed = valid[n // 4 : n // 4 + n // 2] or valid
        depth = float(np.mean(trimmed))
        if return_points:
            return depth, valid_points
        return depth

    @staticmethod
    def _deproject(
        u: float, v: float, depth_m: float, K: np.ndarray,
    ) -> Tuple[float, float, float]:
        fx, fy = K[0, 0], K[1, 1]
        cx, cy = K[0, 2], K[1, 2]
        return (
            (u - cx) * depth_m / fx,
            (v - cy) * depth_m / fy,
            depth_m,
        )

    def _to_torso(self, x: float, y: float, z: float) -> Tuple[float, float, float]:
        """将相机坐标转换到 Torso 坐标(使用内嵌的变换矩阵)。"""
        if self._use_torso_transform:
            return _camera_coords_to_torso(x, y, z)
        # 旧模式:仅平移
        return (x + self._torso_offset[0], y + self._torso_offset[1], z + self._torso_offset[2])

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #

    def detect_env(self) -> List[str]:
        """当前 D435i 彩色帧上所有类别名(去重、排序)。

        结果会缓存一帧 RGB/深度/YOLO 结果,供随后多次 ``segment_3d`` 复用(同一图像与检测结果),
        直到下次调用本方法或 ``stop()``。
        """
        if not self._started:
            self.start()
        rgb, depth_u16, detections = self._get_frame_detections()
        self._cached_rgb = rgb
        self._cached_depth_u16 = depth_u16
        self._cached_detections = detections
        names = list({str(d["class_name"]) for d in detections if "class_name" in d})
        return sorted(names)

    def segment_3d(self, object_name: str) -> List[Dict[str, Any]]:
        """检测 ``object_name``,返回相机系与 torso 下的 3D 信息。

        ``position_camera``:针孔反投影(OpenCV 相机系,x 右 y 下 z 前,米)。
        ``position_base``:默认经 ``D435ToTorsoTransformer`` 变换到 torso;旧平移模式见环境变量说明。

        若已有 ``detect_env`` 写入的缓存,则一直复用同一帧与 YOLO 结果(可对多个 ``object_name`` 多次调用),
        避免与 ``detect_env`` 的标签列表不一致。无缓存时再抓一帧并请求 YOLO。
        需要全新一帧时请先再次调用 ``detect_env()``(或 ``stop()`` 后再 ``start()``)。
        """
        if not self._started:
            self.start()
        if self._cached_detections is not None:
            rgb = self._cached_rgb
            depth_u16 = self._cached_depth_u16
            detections = self._cached_detections
        else:
            rgb, depth_u16, detections = self._get_frame_detections()

        if depth_u16 is None:
            raise RuntimeError("D435i 深度为空(确认 TCP 流含深度且 enable_depth=True)")

        h, w = rgb.shape[0], rgb.shape[1]
        if depth_u16.shape[0] != h or depth_u16.shape[1] != w:
            raise RuntimeError(
                f"RGB 与深度尺寸不一致: rgb={rgb.shape}, depth={depth_u16.shape}"
            )

        matched = [d for d in detections if str(d.get("class_name", "")) == object_name]
        if not matched:
            return []

        depth_m = depth_u16.astype(np.float32) * self._depth_scale_m
        K = self._build_K(w, h)

        out: List[Dict[str, Any]] = []
        vis_bgr = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR)
        for det in matched:
            xyxy = (
                int(det["x1"]),
                int(det["y1"]),
                int(det["x2"]),
                int(det["y2"]),
            )
            dm, sampled_pts = self._sample_depth_m(depth_m, xyxy, return_points=True)
            if dm is None or dm <= 0:
                continue
            cx_px = (xyxy[0] + xyxy[2]) / 2.0
            # 反投影中心:下边界上方 1/4 处(与深度采样位置一致)
            cy_px = xyxy[3] - (xyxy[3] - xyxy[1]) / 4.0
            pos_cam = self._deproject(cx_px, cy_px, dm, K)
            pos_base = self._to_torso(*pos_cam)
            # 调试可视化:标注检测框、深度采样像素与反投影中心点
            cv2.rectangle(vis_bgr, (xyxy[0], xyxy[1]), (xyxy[2], xyxy[3]), (0, 255, 0), 2)
            for px, py in sampled_pts:
                cv2.circle(vis_bgr, (int(px), int(py)), 2, (255, 255, 0), -1)
            cv2.circle(vis_bgr, (int(round(cx_px)), int(round(cy_px))), 4, (0, 0, 255), -1)
            label = str(det.get("class_name", object_name))
            conf = float(det.get("confidence", 0.0))
            cv2.putText(
                vis_bgr,
                f"{label} {conf:.2f} d={dm:.3f}m",
                (xyxy[0], max(20, xyxy[1] - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
            out.append(
                {
                    "label": str(det["class_name"]),
                    "confidence": float(det.get("confidence", 0.0)),
                    "xyxy": list(xyxy),
                    "position_camera": list(pos_cam),
                    "position_base": list(pos_base),
                    "depth_m": dm,
                }
            )
        if out:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(
                os.path.expanduser("~"),
                f"d435i_segment3d_{object_name}_{ts}.jpg",
            )
            if cv2.imwrite(out_path, vis_bgr):
                logger.info("YoloSDK: segment_3d 深度采样标注图已保存 %s", out_path)
            else:
                logger.warning("YoloSDK: segment_3d 标注图保存失败 %s", out_path)
        return out

    def save_detection_image(self, output_path: Optional[str] = None) -> str:
        """取 D435i 一帧,调用 YOLO,在 RGB 上画框并保存为 JPEG。

        Args:
            output_path: 保存路径;默认 ``~/d435i_yolo_YYYYMMDD_HHMMSS.jpg``

        Returns:
            实际写入的文件路径
        """
        import cv2
        from datetime import datetime

        if not self._started:
            self.start()
        rgb, _depth, detections = self._get_frame_detections()
        vis_bgr = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR)
        for d in detections:
            try:
                x1 = int(float(d["x1"]))
                y1 = int(float(d["y1"]))
                x2 = int(float(d["x2"]))
                y2 = int(float(d["y2"]))
            except (KeyError, TypeError, ValueError):
                continue
            label = str(d.get("class_name", "?"))
            conf = float(d.get("confidence", 0.0))
            cv2.rectangle(vis_bgr, (x1, y1), (x2, y2), (0, 255, 0), 2)
            ty = max(y1 - 8, 24)
            cv2.putText(
                vis_bgr,
                f"{label} {conf:.2f}",
                (x1, ty),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
        if not output_path:
            output_path = os.path.join(
                os.path.expanduser("~"),
                f"d435i_yolo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg",
            )
        out = os.path.abspath(os.path.expanduser(output_path))
        if not cv2.imwrite(out, vis_bgr):
            raise RuntimeError(f"保存失败: {out}")
        logger.info("YoloSDK: 已保存带框图像 %s", out)
        return out

    def segment_camera(self, camera_name: str, label: Optional[str] = None) -> List[Dict[str, Any]]:
        """固定使用 D435i;``camera_name`` 仅作兼容保留。``label`` 有值时等价 ``segment_3d``。"""
        logger.info("segment_camera(%r, %r) - G1 固定 D435i", camera_name, label)
        if label:
            return self.segment_3d(label)
        return []


if __name__ == "__main__":
    import time

    yolo = YoloSDK()
    with yolo:
        for i in range(3):
            labels = yolo.detect_env()
            print("labels:", labels)
            for lb in labels[:2]:
                dets = yolo.segment_3d(lb)
                for d in dets:
                    print(" ", d["label"], d["position_base"], d["depth_m"])
            time.sleep(0.5)
