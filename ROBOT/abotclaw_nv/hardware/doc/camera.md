# Camera Dependencies

两个脚本的环境完全一致，统一依赖如下。

## 运行时环境

| 依赖 | 当前版本 | 版本约束 | 说明 | 安装方式 |
|------|----------|----------|------|----------|
| Python 3.8+ | 3.x | `>=3.8` | 运行时 | 系统自带 |
| librealsense 系统库 | 2.57 | `>=2.50.0` | Intel RealSense SDK，pyrealsense2 的底层依赖 | 需源码编译，见下方详细步骤 |

## Python 包

| 包 | 当前版本 | 版本约束 | 说明 |
|----|----------|----------|------|
| pyrealsense2 | 2.55.1.6486 | `>=2.50.0` | Intel RealSense Python 绑定 |
| numpy | 1.24.4 | `>=1.20` | 数值计算，深度图操作 |
| opencv-python | 4.13.0.92 | `>=4.8` | 图像编解码 (cv2.imencode / imdecode) |
| pyzmq | 26.4.0 | `>=26.0` | ZMQ 消息队列（D455_server.py TCP 推流用） |

安装：

```bash
pip install pyrealsense2>=2.50.0 numpy>=1.20 opencv-python>=4.8 pyzmq>=26.0
```

## librealsense 系统库

| 组件 | 当前版本 | 版本约束 | 说明 |
|------|----------|----------|------|
| librealsense2.so | 2.57 | `>=2.50.0` | Intel RealSense SDK 系统库 |
| pyrealsense2 | 2.55.1.6486 | `>=2.50.0` | Python 绑定，须与系统库版本匹配 |

## librealsense 系统库安装

pyrealsense2 依赖系统级 `librealsense2.so`，需从源码编译：

```bash
# 安装编译依赖
sudo apt-get update
sudo apt-get install -y \
    libgl1-mesa-glx libglfw3 libgl1-mesa-dri \
    libusb-1.0-0-dev pkg-config libgtk-3-dev \
    libgstreamer1.0-dev python3-pip git cmake

# Jetson 平台建议使用 RSUSB backend（避免内核驱动冲突）
git clone https://github.com/IntelRealSense/librealsense.git
cd librealsense
git checkout v2.57
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

### 验证安装

```bash
# 确认库文件存在（当前安装版本: librealsense2.so.2.57）
ls /usr/local/lib/librealsense2.so

# 确认 Python 绑定版本与系统库匹配（当前: pyrealsense2 2.55.1.6486）
python3 -c "import pyrealsense2 as rs; print('pyrealsense2:', rs.__version__)"

# 确认 librealsense 系统库版本（当前: 2.57）
pkg-config --modversion librealsense2
# 或
ldconfig -p | grep librealsense2
```

## 两个脚本的差异

| | g1_camera_server.py | D455_server.py |
|---|---|---|
| 相机类型 | RealSense D435I (序列号 342522073568) | RealSense D455 (序列号 336522303538) |
| 输出方式 | 原生 TCP `socket.sendall` | ZeroMQ PUB |
| 深度对齐 | 关闭（使用原始图像） | 开启（rs.align 到彩色） |
| 默认端口 | 8765 | 5555 (RGB) / 5556 (深度) |
| 依赖额外包 | 无 | pyzmq |

## 常见问题

### Device or resource busy

```bash
# 关闭所有占用进程
pkill -f realsense-viewer
pkill -f g1_camera_server

# Jetson 平台重启 nvargus-daemon
sudo systemctl restart nvargus-daemon
```

### pyrealsense2 导入失败

```bash
# 确认 librealsense 已安装
ls /usr/local/lib/librealsense2.so
sudo ldconfig
```

### Jetson 上找不到 USB 设备

使用 RSUSB backend 重新编译 librealsense，见上方安装步骤。
