"""
G1 D455 Camera SDK

通过 ZMQ 连接 G1 上的 image_server 获取 RGB 和深度图像。

使用方法:
    from g1_d455_camera import G1D455Camera
    import cv2
    import os
    
    client = G1D455Camera()
    client.initialize()
    
    # 获取 RGB + 深度
    rgb, depth = client.get_frame()
    
    # ⚠️ 注意: get_frame() 只返回 numpy 数组，不会自动保存文件！
    # 如需保存图像，必须显式调用 cv2.imwrite:
    if rgb is not None:
        save_path = os.path.expanduser("~/d455_frame.jpg")
        cv2.imwrite(save_path, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        print(f"Saved to {save_path}")
    
    # 获取相机内参
    intrinsics = client.get_intrinsics()
    
    client.close()
"""

import struct
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

try:
    from .config import get_g1_robot_ip
except ImportError:
    from config import get_g1_robot_ip


@dataclass
class CameraIntrinsics:
    """相机内参"""
    fx: float = 386.0
    fy: float = 386.0
    cx: float = 320.0
    cy: float = 240.0
    width: int = 640
    height: int = 480


class G1D455Camera:
    """G1 机器人 D455 相机客户端
    
    通过 ZMQ 订阅 G1 上 image_server 发布的 RGB 和深度图像。
    """
    
    # 默认配置
    DEFAULT_HOST = get_g1_robot_ip()
    DEFAULT_RGB_PORT = 5555
    DEFAULT_DEPTH_PORT = 5556
    FRAME_HEADER = struct.Struct("<dI")  # timestamp (double) + frame_id (uint32)
    
    def __init__(
        self,
        host: Optional[str] = None,
        rgb_port: int = DEFAULT_RGB_PORT,
        depth_port: int = DEFAULT_DEPTH_PORT,
        width: int = 640,
        height: int = 480,
        timeout_ms: int = 5000,
        enable_depth: bool = True,
    ):
        """初始化相机客户端
        
        Args:
            host: G1 机器人 IP 地址
            rgb_port: RGB 图像 ZMQ 端口
            depth_port: 深度图像 ZMQ 端口
            width: 图像宽度
            height: 图像高度
            timeout_ms: 接收超时 (毫秒)
            enable_depth: 是否启用深度接收
        """
        self.host = host or get_g1_robot_ip()
        self.rgb_port = rgb_port
        self.depth_port = depth_port
        self.width = width
        self.height = height
        self.timeout_ms = timeout_ms
        self.enable_depth = enable_depth
        
        self._intrinsics = CameraIntrinsics(width=width, height=height)
        self._initialized = False
        
        # ZMQ 相关
        self._context = None
        self._rgb_socket = None
        self._depth_socket = None
        
        # 缓存
        self._last_rgb: Optional[np.ndarray] = None
    
    def initialize(self) -> bool:
        """初始化 ZMQ 连接
        
        Returns:
            是否初始化成功
        """
        try:
            import zmq
        except ImportError:
            print("[G1D455Camera] 错误: 无法导入 zmq，请安装: pip install pyzmq")
            return False
        
        try:
            self._context = zmq.Context()
            
            # RGB 订阅
            self._rgb_socket = self._context.socket(zmq.SUB)
            self._rgb_socket.setsockopt(zmq.CONFLATE, 1)  # 只保留最新消息
            self._rgb_socket.connect(f"tcp://{self.host}:{self.rgb_port}")
            self._rgb_socket.setsockopt_string(zmq.SUBSCRIBE, "")
            self._rgb_socket.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
            
            # 深度订阅 (可选)
            if self.enable_depth:
                self._depth_socket = self._context.socket(zmq.SUB)
                self._depth_socket.setsockopt(zmq.CONFLATE, 1)
                self._depth_socket.connect(f"tcp://{self.host}:{self.depth_port}")
                self._depth_socket.setsockopt_string(zmq.SUBSCRIBE, "")
                self._depth_socket.setsockopt(zmq.RCVTIMEO, 100)  # 短超时
            
            self._initialized = True
            print(f"[G1D455Camera] 初始化成功 - {self.host}:{self.rgb_port}")
            return True
            
        except Exception as e:
            print(f"[G1D455Camera] 初始化失败: {e}")
            self.close()
            return False
    
    def _decode_header(self, data: bytes) -> Tuple[Optional[float], Optional[int], bytes]:
        """解码帧头
        
        Returns:
            (timestamp, frame_id, payload)
        """
        if len(data) < self.FRAME_HEADER.size:
            return None, None, data
        
        stamp, frame_id = self.FRAME_HEADER.unpack_from(data, 0)
        return float(stamp), int(frame_id), data[self.FRAME_HEADER.size:]
    
    def _receive_rgb(self) -> Optional[Tuple[np.ndarray, Optional[float], Optional[int]]]:
        """接收 RGB 图像
        
        Returns:
            (image, timestamp, frame_id) 或 None
        """
        if self._rgb_socket is None:
            return None
        
        try:
            data = self._rgb_socket.recv()
            stamp, frame_id, payload = self._decode_header(data)
            
            # 解码 JPEG
            img = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                # 兼容旧协议：无帧头
                img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                stamp, frame_id = None, None
            
            if img is not None:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                self._last_rgb = img
                return img, stamp, frame_id
                
        except Exception as e:
            if "timed out" not in str(e).lower():
                print(f"[G1D455Camera] RGB 接收失败: {e}")
        
        return None
    
    def _receive_depth(self, blocking: bool = False) -> Optional[Tuple[np.ndarray, Optional[float], Optional[int]]]:
        """接收深度图像
        
        Args:
            blocking: 是否阻塞等待
        
        Returns:
            (image, timestamp, frame_id) 或 None
        """
        if self._depth_socket is None:
            return None
        
        try:
            if blocking:
                data = self._depth_socket.recv()
            else:
                data = self._depth_socket.recv(zmq.NOBLOCK)
            
            expected_payload = self.height * self.width * 2  # uint16
            
            stamp: Optional[float] = None
            frame_id: Optional[int] = None
            payload = data
            
            # 检查是否有帧头 (12 bytes)
            if len(data) == expected_payload + self.FRAME_HEADER.size:
                stamp, frame_id, payload = self._decode_header(data)
            elif len(data) == expected_payload:
                # 无帧头，纯数据
                pass
            else:
                print(f"[G1D455Camera] 深度数据大小异常: {len(data)} bytes")
                return None
            
            img = np.frombuffer(payload, dtype=np.uint16).reshape(self.height, self.width)
            img = img.astype(np.float32) / 1000.0  # mm -> m
            return img, stamp, frame_id
                
        except zmq.Again:
            pass  # 非阻塞，无数据
        except Exception as e:
            print(f"[G1D455Camera] 深度接收错误: {e}")
        
        return None
    
    def get_frame(self) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """获取一帧 RGB 和深度图像
        
        Returns:
            (rgb, depth): 
                - rgb: (H, W, 3) uint8 RGB 图像
                - depth: (H, W) float32 深度图，单位米
        """
        if not self._initialized:
            print("[G1D455Camera] 错误: 未初始化")
            return None, None
        
        # 接收 RGB (阻塞)
        rgb_result = self._receive_rgb()
        if rgb_result is None:
            return self._last_rgb, None
        
        rgb, rgb_stamp, rgb_id = rgb_result
        
        # 接收深度 (阻塞等待，与 RGB 同步)
        depth = None
        if self.enable_depth:
            depth_result = self._receive_depth(blocking=True)
            if depth_result is not None:
                depth, depth_stamp, depth_id = depth_result
        
        return rgb, depth
    
    def get_rgb(self) -> Optional[np.ndarray]:
        """获取 RGB 图像
        
        Returns:
            (H, W, 3) uint8 RGB 图像
        """
        result = self._receive_rgb()
        return result[0] if result else self._last_rgb
    
    def get_depth(self) -> Optional[np.ndarray]:
        """获取深度图像
        
        Returns:
            (H, W) float32 深度图，单位米
        """
        if not self.enable_depth:
            print("[G1D455Camera] 警告: 深度未启用")
            return None
        result = self._receive_depth()
        return result[0] if result else None
    
    def get_intrinsics(self) -> Dict[str, float]:
        """获取相机内参
        
        Returns:
            {fx, fy, cx, cy, width, height}
        """
        return {
            "fx": self._intrinsics.fx,
            "fy": self._intrinsics.fy,
            "cx": self._intrinsics.cx,
            "cy": self._intrinsics.cy,
            "width": self._intrinsics.width,
            "height": self._intrinsics.height,
        }
    
    def set_intrinsics(self, fx: float, fy: float, cx: float, cy: float):
        """设置相机内参"""
        self._intrinsics.fx = fx
        self._intrinsics.fy = fy
        self._intrinsics.cx = cx
        self._intrinsics.cy = cy
    
    def close(self):
        """关闭连接"""
        if self._rgb_socket:
            self._rgb_socket.close()
            self._rgb_socket = None
        if self._depth_socket:
            self._depth_socket.close()
            self._depth_socket = None
        if self._context:
            self._context.term()
            self._context = None
        self._initialized = False
        print("[G1D455Camera] 已关闭")
    
    def __enter__(self):
        self.initialize()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def test_camera():
    """测试相机连接"""
    import argparse
    
    parser = argparse.ArgumentParser(description="测试 G1 D455 相机")
    parser.add_argument("--host", default=get_g1_robot_ip(), help="G1 IP 地址")
    parser.add_argument("--no-depth", action="store_true", help="不接收深度")
    args = parser.parse_args()
    
    print("=" * 60)
    print("G1 D455 相机测试")
    print("=" * 60)
    
    camera = G1D455Camera(host=args.host, enable_depth=not args.no_depth)
    
    if not camera.initialize():
        print("初始化失败!")
        return
    
    print("\n按 'q' 退出, 's' 保存图像\n")
    
    frame_count = 0
    try:
        while True:
            rgb, depth = camera.get_frame()
            
            if rgb is not None:
                # 显示 RGB
                rgb_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                cv2.imshow("RGB", rgb_bgr)
                frame_count += 1
            
            if depth is not None:
                # 深度可视化
                depth_vis = (depth / 6.0 * 255).clip(0, 255).astype(np.uint8)
                depth_color = cv2.applyColorMap(depth_vis, cv2.COLORMAP_JET)
                cv2.imshow("Depth", depth_color)
            
            key = cv2.waitKey(30) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('s') and rgb is not None:
                cv2.imwrite(f"rgb_{frame_count}.png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                if depth is not None:
                    np.save(f"depth_{frame_count}.npy", depth)
                print(f"已保存帧 {frame_count}")
    
    finally:
        camera.close()
        cv2.destroyAllWindows()
        print(f"\n总共接收 {frame_count} 帧")


if __name__ == "__main__":
    test_camera()
