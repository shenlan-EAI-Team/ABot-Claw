"""
G1 D435i Camera SDK

与 ``AbotClaw/src/unitree_g1_grasp/network_camera.py`` / ``main.py``（stream_port）一致：
通过 **TCP** 连接相机服务，按帧接收 **JPEG 彩色 + Z16 深度（uint16 原始缓冲区）**。
无需标定文件即可取图。

**与 YOLO 的关系**：G1_Yolo 只负责 ``POST /detect``；``YoloSDK`` 的图来自本客户端连接的 **D435i TCP 流**。

**断线重连**：单帧 JPEG 解码失败或读包异常时，本客户端会丢弃连接；**下一次** ``get_frame()`` 会自动 ``initialize()`` 再读，**不要求**推流进程重启（推流侧持续监听即可）。

关闭连接时会先 ``shutdown`` 再 ``close``，减轻推流端对已断开连接 ``send`` 时出现 ``Broken pipe`` 的概率。

若长期无法连接，再检查 ``G1_D435I_HOST`` / ``G1_D435I_PORT`` 与 ``nc -vz <host> <port>``。
环境变量 ``G1_D435I_SOCKET_TIMEOUT``（秒，默认 15）可加大单次 ``recv`` 等待，缓解首帧大包或弱网下的 ``TimeoutError``。

**进程内单例（默认）**：相同 ``(host, port)`` 多次构造 ``G1D435iCamera(...)`` 返回 **同一对象**、**同一条 TCP**，
避免 ``YoloSDK()`` 与 wrapper 里再 ``new`` 一路导致第二连接 ``recv`` 超时。需要独立实例时传 ``shared=False``。

**多客户端**：推流端若仅服务单路，除上述单例外仍应避免 **HTTP 列表相机** 与子进程 **同时** 各连 8765。

使用方法:
    from g1_d435i_camera import G1D435iCamera
    import cv2
    import os

    cam = G1D435iCamera()
    if cam.initialize():
        rgb, depth = cam.get_frame()   # rgb: (H,W,3) uint8 RGB; depth: (H,W) uint16 或 None
        
        # ⚠️ 注意: get_frame() 只返回 numpy 数组，不会自动保存文件！
        # 如需保存图像，必须显式调用 cv2.imwrite:
        if rgb is not None:
            save_path = os.path.expanduser("~/d435i_frame.jpg")
            cv2.imwrite(save_path, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            print(f"Saved to {save_path}")
        
        cam.close()
"""

from __future__ import annotations

import errno
import os
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

try:
    from .config import get_g1_d435i_host, get_g1_d435i_port
except ImportError:
    from config import get_g1_d435i_host, get_g1_d435i_port

# 协议与 AbotClaw ``network_camera.StreamingCamera`` 一致
_MAX_PAYLOAD = 10 * 1024 * 1024


def _default_socket_timeout_s() -> float:
    v = os.environ.get("G1_D435I_SOCKET_TIMEOUT", "15.0")
    try:
        return max(1.0, float(v))
    except ValueError:
        return 15.0


@dataclass
class CameraIntrinsics:
    """D435i 内参（1280×720 分辨率）。

    来源: unitree_g1_grasp/d435i_calibration.json
    ``fx, fy, cx, cy`` 与 ``width, height`` 表示 **同一参考分辨率** 下的内参。
    首帧解码得到真实 ``(w,h)`` 时，若与当前 ``width/height`` 不一致，会按比例缩放
    ``fx, fy, cx, cy``，使针孔模型与像素坐标系一致。
    """

    fx: float = 650.0841064453125
    fy: float = 650.0841064453125
    cx: float = 644.9938354492188
    cy: float = 358.162353515625
    width: int = 1280
    height: int = 720


class G1D435iCamera:
    """G1 D435i：TCP 流相机客户端（与 unitree_g1_grasp 中 ``create_camera`` 协议相同）。"""

    DEFAULT_HOST = get_g1_d435i_host()
    DEFAULT_PORT = get_g1_d435i_port()
    DEFAULT_TIMEOUT = _default_socket_timeout_s()
    _singleton_registry: dict[tuple[str, int], G1D435iCamera] = {}
    _singleton_lock = threading.Lock()

    def __new__(
        cls,
        host: Optional[str] = None,
        port: Optional[int] = None,
        stream_port: Optional[int] = None,
        timeout: Optional[float] = None,
        enable_depth: bool = True,
        rgb_port: Optional[int] = None,
        depth_port: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[int] = None,
        depth_format: Optional[str] = None,
        shared: bool = True,
    ) -> G1D435iCamera:
        port_i = int(stream_port if stream_port is not None else (port if port is not None else cls.DEFAULT_PORT))
        host_s = str(host or get_g1_d435i_host())
        if not shared:
            inst = super().__new__(cls)
            inst._singleton_key = None
            return inst
        key = (host_s, port_i)
        with cls._singleton_lock:
            existing = cls._singleton_registry.get(key)
            if existing is not None:
                return existing
            inst = super().__new__(cls)
            cls._singleton_registry[key] = inst
            inst._singleton_key = key
            return inst

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        stream_port: Optional[int] = None,
        timeout: Optional[float] = None,
        enable_depth: bool = True,
        # 以下为旧版 GStreamer API 残留参数，忽略以保持兼容
        rgb_port: Optional[int] = None,
        depth_port: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[int] = None,
        depth_format: Optional[str] = None,
        shared: bool = True,
    ):
        """
        Args:
            host: 相机服务所在 IP（一般为 G1 或跑相机节点的机器）
            port: TCP 端口，默认 8765（与 ``main.py --stream_port`` 一致）
            stream_port: 与 ``port`` 相同含义，二者择一即可
            timeout: 套接字超时（秒）；默认读 ``G1_D435I_SOCKET_TIMEOUT`` / ``DEFAULT_TIMEOUT``
            enable_depth: 为 False 时仍接收深度字节以保持流同步，但 ``get_frame`` 返回的深度为 None
            shared: 为 True（默认）时，同一进程内相同 ``(host, port)`` 复用 **同一实例**；测试需独立连接时设 False。
        """
        with type(self)._singleton_lock:
            if getattr(self, "_g1_d435i_inited", False):
                return
            self.host = host or get_g1_d435i_host()
            self.port = int(stream_port if stream_port is not None else (port if port is not None else self.DEFAULT_PORT))
            self.timeout = float(timeout if timeout is not None else self.DEFAULT_TIMEOUT)
            self.enable_depth = enable_depth

            self._intrinsics = CameraIntrinsics(
                width=width or 1280,
                height=height or 720,
            )
            self._socket: Optional[socket.socket] = None
            self._initialized = False
            self._last_rgb: Optional[np.ndarray] = None
            self._last_depth: Optional[np.ndarray] = None
            self._g1_d435i_inited = True

    def _has_active_connection(self) -> bool:
        """是否已有可用 TCP（有则 ``initialize()`` / ``get_frame()`` 不再发起新连接）。"""
        if self._socket is None:
            if self._initialized:
                self._initialized = False
            return False
        if not self._initialized:
            return False
        try:
            err = self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if err != 0:
                return False
        except OSError:
            return False
        return True

    def _invalidate_connection(self) -> None:
        """丢弃当前 TCP，使下次 ``get_frame`` / ``initialize`` 可重新连接（推流侧正常时仍可能因单帧损坏需重同步）。"""
        self._initialized = False
        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def initialize(self) -> bool:
        """连接相机 TCP 服务。已有可用连接时直接返回 True（不重复 connect），便于与 ``YoloSDK(camera=...)`` 共用同一实例。"""
        if self._has_active_connection():
            return True
        self._invalidate_connection()
        try:
            print(f"[G1D435iCamera] 正在连接 {self.host}:{self.port} ...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))
            self._socket = sock
            self._initialized = True
            print(f"[G1D435iCamera] 已连接 {self.host}:{self.port}")
            return True
        except OSError as e:
            print(f"[G1D435iCamera] 连接失败: {e}")
            if getattr(e, "errno", None) == errno.ECONNREFUSED:
                print(
                    "[G1D435iCamera] 提示: Connection refused 多为「本 IP:端口无进程监听」或连错主机。"
                    " 推流若在其它机器，请在 robot_sdk/config.yaml 设置 g1.d435i_host；"
                    " 若在机器人本机，请确认推流进程监听 0.0.0.0:端口（勿仅 127.0.0.1），且端口与 g1.d435i_port 一致。"
                )
            self._invalidate_connection()
            return False

    def _recv_exact(self, n: int) -> bytes:
        if self._socket is None:
            raise ConnectionError("socket 未连接")
        buf = bytearray()
        while len(buf) < n:
            chunk = self._socket.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("连接已关闭")
            buf.extend(chunk)
        return bytes(buf)

    def _fetch_one_frame(self) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """读一帧：8 字节头 (color_len, depth_len) + JPEG + raw uint16 depth。"""
        if not self._has_active_connection():
            return None, None
        try:
            header = self._recv_exact(8)
            color_size, depth_size = struct.unpack("<II", header)
            if color_size > _MAX_PAYLOAD or depth_size > _MAX_PAYLOAD:
                print(f"[G1D435iCamera] 数据包异常: color={color_size}, depth={depth_size}")
                self._invalidate_connection()
                return None, None

            color_data = self._recv_exact(color_size) if color_size else b""
            depth_data = self._recv_exact(depth_size) if depth_size else b""

            if not color_data:
                print("[G1D435iCamera] 彩色数据为空")
                self._invalidate_connection()
                return None, None

            color_bgr = cv2.imdecode(np.frombuffer(color_data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if color_bgr is None:
                print("[G1D435iCamera] 彩色 JPEG 解码失败")
                self._invalidate_connection()
                return None, None

            rgb = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[0], rgb.shape[1]
            prev_w = int(self._intrinsics.width)
            prev_h = int(self._intrinsics.height)
            if prev_w > 0 and prev_h > 0 and (prev_w != w or prev_h != h):
                sx = float(w) / float(prev_w)
                sy = float(h) / float(prev_h)
                self._intrinsics.fx *= sx
                self._intrinsics.fy *= sy
                self._intrinsics.cx *= sx
                self._intrinsics.cy *= sy
            self._intrinsics.width = w
            self._intrinsics.height = h

            depth: Optional[np.ndarray] = None
            if depth_size and self.enable_depth:
                depth_flat = np.frombuffer(depth_data, dtype=np.uint16)
                expected = h * w
                if depth_flat.size != expected:
                    print(
                        f"[G1D435iCamera] 深度长度与彩色分辨率不一致: "
                        f"got={depth_flat.size}, expected={expected} ({w}x{h})"
                    )
                    depth = None
                else:
                    depth = depth_flat.reshape((h, w)).copy()

            return rgb, depth
        except Exception as e:
            print(f"[G1D435iCamera] 取帧失败: {type(e).__name__}: {e}")
            self._invalidate_connection()
            return None, None

    def get_frame(self) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """获取一帧 RGB 与深度。

        已有活动连接时**不会**再次 ``connect``，直接 ``recv`` 取一帧。仅当无连接或已失效时才 ``initialize()``。

        Returns:
            rgb: (H, W, 3) uint8，RGB 顺序
            depth: (H, W) uint16（Z16 原始值，单位取决于相机服务）；未开深度或失败时为 None
        """
        if not self._has_active_connection():
            if not self.initialize():
                return None, None

        rgb, depth = self._fetch_one_frame()
        if rgb is not None:
            self._last_rgb = rgb
        if depth is not None:
            self._last_depth = depth
        if not self.enable_depth:
            depth = None

        # 单帧失败已 invalidate：无活动连接时再 initialize() 重连并读一帧
        if rgb is None and not self._has_active_connection():
            if self.initialize():
                rgb, depth = self._fetch_one_frame()
                if not self.enable_depth:
                    depth = None

        return rgb, depth

    def get_rgb(self) -> Optional[np.ndarray]:
        rgb, _ = self.get_frame()
        return rgb

    def get_depth(self) -> Optional[np.ndarray]:
        if not self.enable_depth:
            print("[G1D435iCamera] 警告: 深度未启用")
            return None
        _, depth = self.get_frame()
        return depth

    def get_intrinsics(self) -> Dict[str, float]:
        return {
            "fx": self._intrinsics.fx,
            "fy": self._intrinsics.fy,
            "cx": self._intrinsics.cx,
            "cy": self._intrinsics.cy,
            "width": float(self._intrinsics.width),
            "height": float(self._intrinsics.height),
        }

    def set_intrinsics(self, fx: float, fy: float, cx: float, cy: float) -> None:
        self._intrinsics.fx = fx
        self._intrinsics.fy = fy
        self._intrinsics.cx = cx
        self._intrinsics.cy = cy

    def close(self) -> None:
        self._invalidate_connection()
        print("[G1D435iCamera] 已关闭")

    def __enter__(self) -> "G1D435iCamera":
        self.initialize()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _depth_to_bgr_vis(depth: np.ndarray) -> np.ndarray:
    """Z16 (H,W) → BGR 伪彩色，便于 imshow。"""
    if depth.ndim != 2:
        return cv2.cvtColor(depth, cv2.COLOR_RGB2BGR)
    d = depth.astype(np.float32)
    d = np.where(d > 0, d, np.nan)
    lo, hi = np.nanpercentile(d, [2, 98])
    if not np.isfinite(lo) or hi <= lo:
        lo, hi = 0.0, float(np.nanmax(d)) if np.isfinite(np.nanmax(d)) else 1.0
    vis = np.clip((d - lo) / (hi - lo + 1e-6), 0, 1)
    vis = np.nan_to_num(vis, nan=0.0)
    vis_u8 = (vis * 255).astype(np.uint8)
    return cv2.applyColorMap(vis_u8, cv2.COLORMAP_TURBO)


def test_camera() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="测试 G1 D435i TCP 相机（与 unitree_g1_grasp 一致）")
    parser.add_argument("--host", default=G1D435iCamera.DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=G1D435iCamera.DEFAULT_PORT, help="TCP 流端口，同 main.py --stream_port")
    parser.add_argument("--no-depth", action="store_true", help="只拉 RGB，不在结果中返回深度（仍接收字节）")
    args = parser.parse_args()

    print("=" * 60)
    print("G1 D435i TCP 流测试")
    print("=" * 60)

    camera = G1D435iCamera(host=args.host, port=args.port, enable_depth=not args.no_depth)
    if not camera.initialize():
        print("连接失败")
        return

    print("\n按 'q' 退出, 's' 保存当前帧\n")
    frame_count = 0
    last_time = time.perf_counter()

    try:
        while True:
            rgb, depth = camera.get_frame()
            display_img = None

            if rgb is not None:
                rgb_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                display_img = rgb_bgr
                frame_count += 1

            if depth is not None and display_img is not None:
                display_img = cv2.hconcat([rgb_bgr, _depth_to_bgr_vis(depth)])
            elif depth is not None:
                display_img = _depth_to_bgr_vis(depth)

            if display_img is not None:
                now = time.perf_counter()
                fps = 1.0 / (now - last_time) if (now - last_time) > 0 else 0.0
                last_time = now
                cv2.putText(
                    display_img,
                    f"FPS: {fps:.1f}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 255, 0),
                    2,
                )
                cv2.imshow("G1 D435i (TCP)", display_img)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s") and rgb is not None:
                cv2.imwrite(f"rgb_{frame_count}.png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                if depth is not None:
                    cv2.imwrite(f"depth_{frame_count}.png", depth)
                    np.save(f"depth_{frame_count}.npy", depth)
                    print(f"已保存 rgb/depth png + depth npy（帧 {frame_count}）")
                else:
                    print(f"已保存 rgb（帧 {frame_count}）")
    finally:
        camera.close()
        cv2.destroyAllWindows()
        print(f"\n共显示约 {frame_count} 帧 RGB")


if __name__ == "__main__":
    test_camera()
