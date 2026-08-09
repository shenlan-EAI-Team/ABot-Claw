# ABot-Claw

异构机器人舰队控制平台。基于 FastAPI Agent Server 构建的 AI 推理引擎，通过 HTTP 接口接收高层指令，将 AI 推理结果桥接到机器人的运动执行、感知和导航能力。

当前支持 **Unitree G1** 人形机器人。

---

## 系统架构

ABot-Claw 由三层组成，各层独立部署：

```
┌─────────────────────────────────────────────────────────────┐
│                  openclaw_layer                              │
│  AI Agent 工作空间 — OpenClaw agent（人形机器人 Abot）       │
│  ├── ROBOT.md  ── Fleet 硬规则、调用路径、SDK 预注入对象    │
│  ├── MISSION.md ── 任务立场与决策流程                       │
│  ├── IDENTITY.md ── Agent 身份与原则                       │
│  └── skills/       ── 可复用 Skill 定义                     │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP API
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                   robot_layer                                │
│  Agent Server (FastAPI :8888) — 机器人能力网关              │
│  ├── /code/execute   ── 沙箱 Python 执行（预注入 env/yolo/..）│
│  ├── /lease/*       ── 执行令牌管理（防并发）                 │
│  ├── /nav/*         ── ROS1 导航                            │
│  ├── /camera/*      ── D455/D435i 相机控制                  │
│  ├── /face/*        ── 人脸识别                              │
│  ├── /memory/*      ── 空间记忆                              │
│  ├── /grasp/*       ── 抓取任务                              │
│  ├── /tts/*         ── 语音合成                              │
│  ├── robot_ros_ws/  ── ROS1 Noetic 导航栈                   │
│  │       ├── fastlio2     ── FAST-LIO2 3D SLAM             │
│  │       ├── livox_ros_driver2 ── Livox MID360 雷达驱动     │
│  │       └── movebase     ── MoveBase + TEB 规划器          │
│  ├── robot_sdk/    ── G1 能力 SDK（DDS / TCP 控制机器人）   │
│  └── hardware/      ── 相机推流服务、灵巧手控制              │
└──────────┬──────────────────────┬───────────────────────────┘
           │ HTTP                │ DDS / TCP
           ▼                     ▼
┌─────────────────────┐  ┌──────────────────────────────────────┐
│   services_layer    │  │              硬件层                   │
│  感知服务（Docker）  │  │  ├── Unitree G1 人形机器人            │
│  ├── YOLO :8013     │  │  ├── Livox MID360 LiDAR              │
│  ├── AnyGrasp :8015 │  │  ├── RealSense D455（头部水平朝前）   │
│  ├── Face :8016     │  │  ├── RealSense D435i（倾斜朝下）      │
│  └── Memory :8022   │  └── LinkerHand 灵巧手                 │
└─────────────────────┘
```

---

## 目录结构

```
abotclaw_nv/
│
├── robot_layer/                  # 机器人控制层
│   ├── config.env                # 全局配置（IP、网卡、环境路径）
│   ├── LICENSE                   # 非商业许可证（AGPL-3.0）
│   ├── Dockerfile.abotclaw       # 镜像构建（Ubuntu 20.04 + ROS1 Noetic）
│   │
│   └── robot_client/unitree_G1/
│       ├── agent_server/        # FastAPI Agent Server
│       │   ├── server.py        # 主入口（Uvicorn :8888）
│       │   ├── code_executor.py # 沙箱代码执行器
│       │   ├── lease.py         # 执行令牌管理
│       │   ├── state.py         # 机器人状态聚合
│       │   ├── safety.py        # 安全限制
│       │   ├── robot_sdk/       # G1 能力 SDK 封装
│       │   │   ├── g1_robot_env.py    # 机器人环境主接口
│       │   │   ├── navigation_sdk.py  # ROS1 导航客户端
│       │   │   ├── g1_grasp_sdk.py   # 双臂抓取
│       │   │   ├── yolo_sdk.py       # YOLO 目标检测
│       │   │   ├── face_sdk.py       # 人脸识别
│       │   │   ├── hand_sdk.py       # 灵巧手控制
│       │   │   ├── tts_sdk.py        # 语音合成
│       │   │   ├── vision_sdk.py     # 多模态场景描述
│       │   │   ├── memory_sdk.py     # 空间记忆
│       │   │   ├── g1_d435i_camera.py # D435i 客户端
│       │   │   ├── g1_d455_camera.py  # D455 客户端
│       │   │   ├── grasp_something_sdk.py  # 一体化抓取
│       │   │   ├── release_something_sdk.py # 完整放置
│       │   │   ├── robotbridge.py    # DDS 桥接
│       │   │   └── config.yaml       # 机器人参数配置
│       │   │
│       │   ├── routes/           # HTTP 路由
│       │   │   ├── camera_routes.py
│       │   │   ├── code_routes.py
│       │   │   ├── face_routes.py
│       │   │   ├── grasp_routes.py
│       │   │   ├── lease_routes.py
│       │   │   ├── memory_routes.py
│       │   │   ├── navigation_routes.py
│       │   │   ├── service_routes.py
│       │   │   ├── state_routes.py
│       │   │   ├── tts_routes.py
│       │   │   ├── ws.py              # WebSocket
│       │   │   ├── yolo_routes.py
│       │   │   └── sdk_docs.py
│       │   │
│       │   └── robot_ros_ws/    # ROS1 导航工作空间
│       │       ├── map/         # 地图文件（yaml/pgm/pcd）
│       │       └── WK/
│       │           ├── G1Nav2D/src/
│       │           │   ├── fastlio2/          # FAST-LIO2 3D SLAM
│       │           │   ├── livox_ros_driver2/ # Livox 雷达驱动
│       │           │   ├── movebase/          # 导航栈 + TEB 规划器
│       │           │   └── pointcloud_to_laserscan/
│       │           └── unitree_sdk2_python/  # Unitree SDK2 Python
│       │
│       ├── hardware/            # 硬件集成
│       │   ├── camera/
│       │   │   ├── D455_server.py       # D455 ZeroMQ 推流
│       │   │   └── g1_camera_server.py  # D435i TCP 推流
│       │   ├── linkhand/
│       │   │   ├── hand_server.py       # TCP 服务入口
│       │   │   └── start_hand_server.sh # 启动脚本
│       │   └── scripts/              # 服务管理
│       │
│       └── unitree_sdk2_python/  # Unitree SDK2（顶层）
│
├── openclaw_layer/             # AI Agent 工作空间层
│   └── workspace/
│       ├── ROBOT.md            # Fleet 硬规则（唯一真相源）
│       ├── MISSION.md          # Agent 任务立场与决策流程
│       ├── IDENTITY.md         # Agent 身份定义（Abot）
│       ├── AGENTS.md           # Agent 配置
│       ├── HEARTBEAT.md        # 心跳机制
│       ├── SOUL.md             # Agent 灵魂定义
│       ├── TOOLS.md            # 本地环境笔记
│       ├── USER.md             # 用户信息
│       ├── docs/               # 文档（TidyBot Bundle、SDK Discovery 等）
│       ├── skills/             # Agent 技能
│       └── .openclaw/          # OpenClaw 工作区状态
│
└── services_layer/             # 感知服务层
    ├── docker-compose.yml      # Docker 编排（全部 4 个服务）
    ├── Dockerfile              # 全合一镜像（CUDA 12.4 + 多 Conda 环境）
    ├── start_services.sh       # 统一启动脚本
    ├── stop_services.sh        # 停止脚本
    │
    ├── YOLO/                   # YOLOv5 目标检测（端口 8013）
    │   ├── main.py             # FastAPI 入口
    │   ├── service.yaml        # 服务配置
    │   └── yolov5l6.pt         # 模型（~147MB）
    │
    ├── GraspAnything/          # AnyGrasp 抓取检测（端口 8015）
    │   ├── main.py
    │   └── service.yaml
    │
    ├── face_recognition_insightface/  # InsightFace 人脸识别（端口 8016）
    │   ├── main.py
    │   ├── service.yaml
    │   └── buffalo_l/          # 预训练模型
    │
    └── SpatialMemory/          # 统一空间记忆（端口 8022）
        ├── main.py
        ├── service.yaml
        ├── memory_hub.py       # SQLite 记忆中枢
        └── schemas/            # API 数据模型
```

---

## 开箱即用镜像

我们提供完整的**电脑主机端**和**机器人端** Docker 镜像，开箱即用，无需手动配置任何依赖。如果需要镜像，请联系我们。

---

## 快速启动

### 1. 启动感知服务（宿主机，Docker）

```bash
cd services_layer
docker build -t services-all-in-one:latest .
docker compose up -d
```

验证服务状态：

```bash
curl http://localhost:8013/health   # YOLO
curl http://localhost:8016/health   # 人脸识别
curl http://localhost:8015/health   # AnyGrasp
curl http://localhost:8022/health   # 空间记忆
```

### 2. 启动 Agent Server（容器内）

```bash
# 加载镜像（首次）
gunzip -c abotclaw_g1_final.tar.gz | docker load

# 启动新容器
docker run -it --rm \
    --name AbotClaw \
    --network=host \
    --privileged \
    -e "TERM=xterm-256color" \
    -v /home/unitree/abotclaw_nv:/home/unitree/abotclaw_nv \
    abotclaw_g1_final \
    bash

# === 容器内，7 个终端 ===
```

#### 终端 1：Agent Server

```bash
cd robot_client/unitree_G1/agent_server
./start_server.sh
# ✅ Uvicorn running on http://0.0.0.0:8888
```

#### 终端 2 & 3：相机推流（修改序列号后启动）

```bash
python3 hardware/camera/D455_server.py         # D455，水平朝前，人脸/场景
python3 hardware/camera/g1_camera_server.py   # D435i，倾斜朝下，抓取专用
```

#### 终端 4：导航系统

```bash
source /opt/ros/noetic/setup.sh
cd /home/unitree/abotclaw_nv/navigate/WK/G1Nav2D
source devel/setup.bash
export ROS_MASTER_URI=http://<机器人IP>:11311
roslaunch fastlio navigation.launch
```

#### 终端 5：初始重定位

```bash
rosservice call /slam_reloc "{pcd_path: '/home/unitree/abotclaw_nv/navigate/map/map.pcd', x: 0.0, y: 0.0, z: 0.0, roll: 0.0, pitch: 0.0, yaw: 0.0}"
```

#### 终端 6：速度控制节点

```bash
source /opt/ros/noetic/setup.sh
export CYCLONEDDS_HOME=/home/unitree/abotclaw_nv/unitree_sdk2_python/cyclonedds/install
python3 unitree_sdk2_python/example/g1/high_level/g1_control_vel.py eth0
```

#### 终端 7：灵巧手服务

```bash
./hardware/scripts/start_hand_server.sh
# ✅ Hand server started on port 5678
```

---

## 核心调用路径

Agent Server 提供三级调用路径：


| Level       | 场景                                 | 方式                                                  |
| ----------- | ---------------------------------- | --------------------------------------------------- |
| **Level 1** | 短 HTTP，只读，不动机器人                    | `curl` 健康检查 / lease / 查询位姿                          |
| **Level 2** | lease + 机体运动 / 感知 / 导航 / Python 逻辑 | `lease/acquire` → `/code/execute` → `lease/release` |
| **Level 3** | 只读 GET 发现                          | `curl -s GET /openapi.json` 等                       |


详细规则见 `openclaw_layer/workspace/ROBOT.md`。

---

## 相机绑定规则


| 相机        | 安装位置     | 用途          | 对应对象                                                       |
| --------- | -------- | ----------- | ---------------------------------------------------------- |
| **D455**  | 头部水平朝前   | 人脸识别 + 场景描述 | `camera` / `face`                                          |
| **D435i** | 手臂附近倾斜朝下 | 仅抓取         | `camera_d435i` / `yolo.detect_env()` / `grasp_something()` |


---

## 硬件配置


| 组件                 | IP / 配置                    |
| ------------------ | -------------------------- |
| G1 人形机器人           | `192.168.123.164`          |
| Livox MID360 LiDAR | `192.168.123.120`          |
| LinkerHand 灵巧手     | TCP `192.168.123.164:5678` |
| D455 / D435i       | 随机器人                       |
| Agent Server       | `http://0.0.0.0:8888`      |


网卡名：`eth0`（`robot_layer/config.env` 中 `G1_IFACE` 配置）。

---

## 安全限制


| 参数           | 限制          |
| ------------ | ----------- |
| 最大前进速度       | 1.0 m/s     |
| 最大侧向速度       | 0.5 m/s     |
| 最大旋转角速度      | 1.0 rad/s   |
| 身体高度范围       | 0.5 – 0.8 m |
| 代码执行超时       | 180 s       |
| Lease 最大持续时间 | 60 s        |


> 真机直接执行，无仿真兜底。运动前确认 **1.5m 范围内无人**。

---

## 开源协议

本项目基于 **GNU Affero General Public License v3.0 (AGPL-3.0)** 开源。

- **允许**：免费使用、修改、再分发
- **要求**：衍生作品必须同样以 AGPL-3.0 开源，代码改动需披露
- **禁止**：商业使用（商业授权请联系版权方）

完整条款见 [LICENSE](robot_layer/LICENSE) 或 [AGPL-3.0 官方原文](https://www.gnu.org/licenses/agpl-3.0.html)。