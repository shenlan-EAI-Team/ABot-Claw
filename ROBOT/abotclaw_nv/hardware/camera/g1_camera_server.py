#!/usr/bin/env python3
"""
G1 机器人相机服务器 - 强制模式
直接通过RealSense SDK访问相机，绕过video设备占用
"""
import os
import sys
import argparse
import time
import socket
import struct
import threading
import signal

REALSENSE_IMPORT_ERROR = ""

try:
    import pyrealsense2 as rs
    HAS_REALSENSE = True
except ImportError as e:
    HAS_REALSENSE = False
    REALSENSE_IMPORT_ERROR = str(e).strip()
    _msg = REALSENSE_IMPORT_ERROR
    if "GLIBC_" in _msg or "libc.so" in _msg:
        print(
            "[WARN] pyrealsense2 已安装但扩展库无法加载（常见于 pip wheel 与系统 glibc 版本不匹配）"
        )
        print(f"       详情: {_msg}")
    elif "No module named 'pyrealsense2" in _msg or "No module named \"pyrealsense2" in _msg:
        print("[WARN] pyrealsense2 未安装（Python 找不到该包）")
    else:
        print(f"[WARN] pyrealsense2 导入失败: {_msg}")

import numpy as np
import cv2


# 配置
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
# D435I exposes both color and depth at 640x480@30. 1280x720 is limited to
# lower FPS on this device and makes pipeline.start() fail with
# "Couldn't resolve requests" when paired with CAMERA_FPS=30.
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30
INIT_RETRY_TIMES = 3
INIT_RETRY_INTERVAL_SEC = 1.5


def realsense_unavailable_reason():
    """HAS_REALSENSE 为 False 时用于日志/返回给调用方的说明。"""
    if HAS_REALSENSE:
        return ""
    if not REALSENSE_IMPORT_ERROR:
        return "pyrealsense2 不可用"
    err = REALSENSE_IMPORT_ERROR
    if "GLIBC_" in err or "libc.so" in err:
        return (
            "pyrealsense2 与当前系统 C 库不兼容（需在设备上编译安装 librealsense/绑定，"
            "或更换与 glibc 匹配的包）"
        )
    return f"pyrealsense2 不可用: {err}"


class ForcedCameraServer:
    """强制模式相机服务器 - 使用RealSense SDK直接模式"""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.running = False
        self.clients = []
        self.clients_lock = threading.Lock()
        self.pipeline = None
        self.server_socket = None
        self.serial = "342522073568"  # 你的相机序列号
        self.last_init_error = ""

    def check_device(self):
        """检查设备"""
        if not HAS_REALSENSE:
            return False, realsense_unavailable_reason()

        ctx = rs.context()
        devices = ctx.query_devices()

        if len(devices) == 0:
            return False, "未检测到设备"

        for device in devices:
            print(f"  设备: {device.get_info(rs.camera_info.name)}")
            print(f"  序列号: {device.get_info(rs.camera_info.serial_number)}")
            print(f"  PID: {device.get_info(rs.camera_info.product_id)}")

        return True, "设备检测成功"

    def _cleanup_pipeline(self):
        """清理旧 pipeline，避免句柄未释放导致设备忙。"""
        if self.pipeline is not None:
            try:
                self.pipeline.stop()
            except Exception:
                pass
            finally:
                self.pipeline = None

    @staticmethod
    def _is_device_busy_error(err):
        msg = str(err).lower()
        return ("device or resource busy" in msg) or ("errno=16" in msg)

    def init_camera_once(self):
        """初始化相机一次 - 使用序列号直接连接"""
        if not HAS_REALSENSE:
            return False

        try:
            print(f"[INFO] 初始化 RealSense 相机 (序列号: {self.serial})...")

            ctx = rs.context()

            # 尝试通过序列号找到设备
            device = None
            for d in ctx.devices:
                if d.get_info(rs.camera_info.serial_number) == self.serial:
                    device = d
                    break

            if device is None:
                available = [
                    d.get_info(rs.camera_info.serial_number) for d in ctx.devices
                ]
                print(f"[ERROR] 未找到序列号 {self.serial} 的设备")
                print(f"[ERROR] 当前可用设备: {available if available else '无'}")
                return False

            print(f"[INFO] 使用设备: {device.get_info(rs.camera_info.name)}")

            # 创建 pipeline 和 config
            self._cleanup_pipeline()
            self.pipeline = rs.pipeline()
            config = rs.config()

            # 启用指定序列号的设备
            config.enable_device(self.serial)

            # 配置流
            config.enable_stream(
                rs.stream.color,
                CAMERA_WIDTH, CAMERA_HEIGHT,
                rs.format.rgb8, CAMERA_FPS
            )
            config.enable_stream(
                rs.stream.depth,
                CAMERA_WIDTH, CAMERA_HEIGHT,
                rs.format.z16, CAMERA_FPS
            )

            # 启动 - 这会绕过V4L2直接访问USB
            profile = self.pipeline.start(config)

            # 保存深度传感器用于校准
            self.depth_sensor = profile.get_device().first_depth_sensor()
            self.depth_scale = self.depth_sensor.get_depth_scale()

            # [已禁用] 创建对齐对象（深度对齐到彩色）
            self.align = rs.align(rs.stream.color)

            print("[INFO] ✓ 相机启动成功!")
            print("[INFO] [已禁用] 深度-彩色对齐 - 使用原始图像")

            # 预热
            print("[INFO] 预热相机...")
            for i in range(10):
                self.pipeline.wait_for_frames()

            return True

        except Exception as e:
            self.last_init_error = str(e)
            print(f"[ERROR] 相机初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def init_camera(self):
        """初始化相机，失败时自动重试，尽量避免手动插拔。"""
        self.last_init_error = ""

        for i in range(1, INIT_RETRY_TIMES + 1):
            if i > 1:
                print(f"[INFO] 第 {i}/{INIT_RETRY_TIMES} 次重试初始化...")

            if self.init_camera_once():
                return True

            # 初始化失败后，先主动释放旧句柄，避免下一次依旧 busy
            self._cleanup_pipeline()
            time.sleep(INIT_RETRY_INTERVAL_SEC)

        print("[ERROR] 多次重试后仍初始化失败。")
        if self._is_device_busy_error(self.last_init_error):
            print("[建议] 检测到设备忙：")
            print("  1) 先关闭占用进程: pkill -f realsense-viewer; pkill -f g1_camera_server")
            print("  2) Jetson 上重启服务: sudo systemctl restart nvargus-daemon")
            print("  3) 再次启动本脚本；仍失败再考虑插拔相机")
        return False

    def start_server(self):
        """启动TCP服务器"""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        self.server_socket.settimeout(1.0)

        print(f"[INFO] 服务器监听 {self.host}:{self.port}")
        self.running = True

        thread = threading.Thread(target=self._accept_clients, daemon=True)
        thread.start()

    def _accept_clients(self):
        """接受客户端"""
        while self.running:
            try:
                client, addr = self.server_socket.accept()
                print(f"[INFO] 客户端连接: {addr}")
                with self.clients_lock:
                    self.clients.append(client)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[警告] {e}")

    def broadcast_frame(self, color, depth):
        """广播帧"""
        if not self.clients:
            return

        try:
            # 将RGB转换为BGR
            color_bgr = cv2.cvtColor(color, cv2.COLOR_RGB2BGR)
            color_encoded = cv2.imencode('.jpg', color_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])[1]
            depth_bytes = depth.tobytes()

            header = struct.pack('<II', len(color_encoded), len(depth_bytes))
            packet = header + color_encoded.tobytes() + depth_bytes

            disconnected = []
            with self.clients_lock:
                for client in self.clients:
                    try:
                        client.sendall(packet)
                    except Exception as e:
                        print(f"[ERROR] 发送帧失败: {e}")
                        disconnected.append(client)
                for client in disconnected:
                    try:
                        client.close()
                    except:
                        pass
                    self.clients.remove(client)
        except Exception as e:
            print(f"[广播错误] {e}")
            import traceback
            traceback.print_exc()

    def get_frame(self):
        """获取一帧（原始图像，未对齐）"""
        if self.pipeline is None:
            return None, None

        try:
            frames = self.pipeline.wait_for_frames(timeout_ms=5000)

            # # [已禁用] 对齐深度到彩色
            # aligned_frames = self.align.process(frames)
            # color_frame = aligned_frames.get_color_frame()
            # depth_frame = aligned_frames.get_depth_frame()

            # 直接获取原始帧
            color_frame = frames.get_color_frame()
            depth_frame = frames.get_depth_frame()

            if not color_frame or not depth_frame:
                return None, None

            color = np.asanyarray(color_frame.get_data())
            depth = np.asanyarray(depth_frame.get_data())

            # 彩色和深度原始分辨率不同（正常现象）
            # if color.shape != depth.shape:
            #     print(f"[WARN] 尺寸不一致: color={color.shape}, depth={depth.shape}")

            return color, depth

        except Exception as e:
            print(f"[ERROR] get_frame: {e}")
            return None, None

    def run(self):
        """运行"""
        print("\n" + "="*50)
        print("G1 相机服务器 - 强制模式")
        print("="*50)

        print("\n[1/3] 检查设备...")
        ok, msg = self.check_device()
        if not ok:
            print(f"[错误] {msg}")
            return

        print("\n[2/3] 初始化相机...")
        if not self.init_camera():
            print("[错误] 相机初始化失败")
            return

        print("\n[3/3] 启动服务器...")
        self.start_server()

        print("\n" + "="*50)
        print("[就绪] 等待客户端连接...")
        print(f"[端口] {self.port}")
        print("="*50 + "\n")

        frame_count = 0
        last_print = time.time()

        try:
            while self.running:
                color, depth = self.get_frame()

                if color is None:
                    time.sleep(0.1)
                    continue

                frame_count += 1
                self.broadcast_frame(color, depth)

                if time.time() - last_print > 5:
                    with self.clients_lock:
                        num = len(self.clients)
                    print(f"[状态] 帧: {frame_count}, 客户端: {num}")
                    last_print = time.time()

        except KeyboardInterrupt:
            print("\n[停止]")
        finally:
            self.stop()

    def stop(self):
        """停止"""
        print("[停止] 正在停止...")
        self.running = False

        with self.clients_lock:
            for client in self.clients:
                try:
                    client.close()
                except:
                    pass
            self.clients.clear()

        if self.pipeline:
            try:
                self.pipeline.stop()
            except:
                pass

        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass

        print("[停止] 完成")


def main():
    parser = argparse.ArgumentParser(description="G1 相机服务器")
    parser.add_argument('--host', default=DEFAULT_HOST)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--serial', default=None)

    args = parser.parse_args()

    server = ForcedCameraServer(args.host, args.port)
    if args.serial:
        server.serial = args.serial

    signal.signal(signal.SIGINT, lambda s, f: server.stop())
    signal.signal(signal.SIGTERM, lambda s, f: server.stop())

    server.run()


if __name__ == '__main__':
    main()
