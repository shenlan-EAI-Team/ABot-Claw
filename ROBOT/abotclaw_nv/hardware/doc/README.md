# G1 相机服务器

通过 Intel RealSense SDK 直接访问相机（绕过 V4L2 设备占用），通过 TCP 广播彩色图和深度图。

## 依赖
在abotclaw环境中已有
| 依赖 | 说明 | 安装 |
|------|------|------|
| Python 3.8+ | | 系统自带 |
| librealsense | Intel RealSense SDK | 需源码编译 |
| pyrealsense2 | Python 绑定 | pip install |
| numpy | 数值计算 | pip install |
| opencv-python | 图像处理 | pip install |

## 安装

### 1. 编译 librealsense

```bash
# 安装依赖
sudo apt-get update
sudo apt-get install -y \
    libgl1-mesa-glx libglfw3 libgl1-mesa-dri \
    libusb-1.0-0-dev pkg-config libgtk-3-dev \
    libgstreamer1.0-dev python3-pip git cmake

# Jetson 平台建议使用 RSUSB backend，避免内核驱动冲突
git clone https://github.com/IntelRealSense/librealsense.git
cd librealsense
git checkout v2.50.0
mkdir build && cd build
cmake .. \
    -DFORCE_RSUSB_BACKEND=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_GRAPHICAL_EXAMPLES=OFF \
    -DBUILD_PYTHON_BINDINGS=OFF
make -j$(nproc)
sudo make install
sudo ldconfig
```

### 2. 安装 Python 依赖

```bash
pip3 install pyrealsense2 numpy opencv-python
```

## 使用方法

```bash
# 默认配置：监听 0.0.0.0:8765
python3 g1_camera_server.py

# 指定端口
python3 g1_camera_server.py --port 9000

# 指定相机序列号
python3 g1_camera_server.py --serial 342522073568
```

## 数据协议

服务器通过 TCP 端口广播 JPEG 彩色图和原始深度数据，每帧格式如下：

```
+----------------+-------------------+---------------------+
| header (8B)    | color data (N B)  | depth data (M B)    |
+----------------+-------------------+---------------------+
```

- **header**: 小端 8 字节 = `[4字节彩色长度][4字节深度长度]`
- **color data**: JPEG 编码的彩色图（1280×720 RGB → BGR）
- **depth data**: 深度图原始数据（1280×720 uint16，单位 mm）

## 客户端示例

```python
import socket
import struct
import numpy as np
import cv2

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(('192.168.1.100', 8765))  # 改为实际机器人 IP

while True:
    header = b''
    while len(header) < 8:
        header += sock.recv(8 - len(header))

    color_len, depth_len = struct.unpack('<II', header)

    color_data = b''
    while len(color_data) < color_len:
        color_data += sock.recv(color_len - len(color_data))

    depth_data = b''
    while len(depth_data) < depth_len:
        depth_data += sock.recv(depth_len - len(depth_data))

    color = cv2.imdecode(np.frombuffer(color_data, np.uint8), 1)
    depth = np.frombuffer(depth_data, np.uint16).reshape(720, 1280)

    cv2.imshow('color', color)
    cv2.waitKey(1)
```

## 常见问题

### Device or resource busy


```bash
# 关闭所有占用进程
pkill -f realsense-viewer
pkill -f g1_camera_server

# Jetson 平台重启 nvargus-daemon
sudo systemctl restart nvargus-daemon
```
相机被其他进程占用（v4l2、realsense-viewer 等）。或者重新插拔深度相机连线。

### pyrealsense2 导入失败

```bash
# 确认 librealsense 已安装
ls /usr/local/lib/librealsense2.so
sudo ldconfig
```

### Jetson 上找不到 USB 设备

使用 RSUSB backend 重新编译 librealsense，见上方安装步骤第 1 步。

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `8765` | 监听端口 |
| `--serial` | `342522073568` | 相机序列号（留空则自动选择第一个设备） |
