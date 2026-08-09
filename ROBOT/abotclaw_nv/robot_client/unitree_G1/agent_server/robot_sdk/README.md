# G1 Robot SDK 使用说明

## 环境配置

### ROS2 Humble (导航功能需要)

使用 `navigation_sdk.py` 前必须先 source ROS2 环境：

```bash
source /opt/ros/humble/setup.bash
```

**启动 Agent Server 时：**
```bash
cd /home/slxy/szm1/ABot-Claw/robot_client/unitree_G1/agent_server
source /opt/ros/humble/setup.bash
python server.py
```

**代码执行节点中：**
```python
# 如果 agent_server 已在 ROS2 环境中启动，通过 /run 执行的代码会自动继承环境
# 独立脚本需要先 source
import subprocess
subprocess.run(["bash", "-c", "source /opt/ros/humble/setup.bash && python your_script.py"])
```

## 关键发现

### 1. 控制方式
G1 机器人需要使用 **仿真状态作为参考**，而不是真实机器人反馈：

```python
# 正确方式：从仿真状态读取关节值
ik.data.qpos[:] = right_arm_ctrl.qpos
mujoco.mj_forward(ik.model, ik.data)
q_val = ik.data.qpos[sim_jid]  # 从仿真读取

# 错误方式：使用真实机器人反馈
q_val = js[idx_real]['q']  # 不要用这个计算轨迹
```

### 2. DDS 初始化时序
创建 RobotBridge 后需要等待 2 秒让 DDS 稳定：

```python
rb = RobotBridge(iface="enp4s0", domain=0, default_mode=0, kp=20.0, kd=1.0)
time.sleep(2.0)  # 关键！等待 DDS 稳定
```

其中 `kp` / `kd` **只作用在 `send_qpos` / `send_qpos_tau` / `send_impedance` 里写入的关节上**（通常为双臂索引 15–28）。腿、腰等未写入的电机在包内始终保持 `kp=kd=0`，与 Unitree `g1_arm7_sdk` 示例一致，**不要**再使用「初始化时给全部 `motor_cmd` 统一赋大刚度」的旧写法。

每次发送前会从 `rt/lowstate` 同步 `mode_pr`、`mode_machine` 到 `LowCmd`。

### 3. 自适应负载补偿
使用重力补偿 + 误差积分：

```python
# 重力补偿
tau_base = float(ik.data.qfrc_bias[dof])

# 自适应积分
err = float(q_val) - js[idx_real]["q"]
tau_integral[idx_real] += adapt_ki * err * dt_control
tau_integral[idx_real] = np.clip(tau_integral[idx_real], -adapt_clamp, adapt_clamp)
tau_ff[idx_real] = tau_base + tau_integral[idx_real]

# 发送
rb.send_qpos_tau(q_real, tau_ff)
```

## 使用示例

```python
from robot_sdk import HandClient
from utils.ik import G1IKSolver, move_dual_arm_to_waypoints
from robot_sdk.robotbridge import RobotBridge
import numpy as np
import time

# 初始化
rb = RobotBridge(iface="enp4s0", domain=0, default_mode=0, kp=20.0, kd=1.0)
time.sleep(2.0)  # 等待 DDS 稳定

# 定义 waypoints
waypoints = [
    (right_pos, right_quat, left_pos, left_quat),
    ...
]

# 执行轨迹
move_dual_arm_to_waypoints(rb, waypoints)

# 抓取
from robot_sdk.config import get_g1_robot_ip

with HandClient(get_g1_robot_ip(), 5678) as hand:
    hand.send("11")  # 右手抓取

# 释放控制
rb.close()
```

## 高级 SDK

### G1 固定轨迹抓取（`g1_grasp_sdk`）

对外仅暴露 ``grasp_target``：传入左右末端**目标位置**（米），四元数使用内部默认；完整序列为 home → lift → target → 抓 → 回 → 放。

```python
from robot_sdk import grasp_target

grasp_target(
    [0.40, -0.05, -0.04],   # right_pos (x,y,z)
    [-0.003, 0.212, -0.004],  # left_pos
    robot_ip=get_g1_robot_ip(),  # 可选，灵巧手所在 IP；默认读 config.yaml / G1_ROBOT_IP
)
```

Agent Server 同时提供 ``POST /grasp/target``（JSON：``right_pos``、``left_pos``、可选 ``robot_ip``），与上述语义一致。

### 导航 SDK (`navigation_sdk`)

ROS2 导航客户端，订阅 `state_estimation`，发布 `goal_pose`。

```python
import rclpy
from geometry_msgs.msg import PoseStamped
from robot_sdk.navigation_sdk import Nav2Anywhere

rclpy.init()
nav = Nav2Anywhere()
pose = PoseStamped()
pose.header.frame_id = "map"
pose.pose.position.x = 1.0
pose.pose.position.y = 0.0
pose.pose.position.z = 0.0
pose.pose.orientation.w = 1.0  # 仅位置时默认朝向
nav.nav_to_pose(pose)
rclpy.spin(nav)  # 距离目标 < 0.2m 时由 SDK 结束 spin
```

**注意：** 使用导航前必须先 `source /opt/ros/humble/setup.bash`

### 人脸识别 SDK (`face_sdk`)

```python
from robot_sdk import FaceSDK

face = FaceSDK()  # 默认读 config.yaml 的 face.url，当前为 127.0.0.1:8016
with face:
    # 录入人脸
    face.enroll("张三", ["base64_image"])
    
    # 识别当前画面
    result = face.recognize_current_frame()
    print(result)  # {"name": "张三", "confidence": 0.95}
```

### 空间记忆 SDK (`memory_sdk`)

```python
from robot_sdk import MemorySDK, Pose

mem = MemorySDK()  # 默认读 config.yaml 的 spatial_memory.url，当前为 127.0.0.1:8022

# 保存物体记忆
mem.upsert_object(
    object_name="red_cup",
    robot_id="g1_001",
    robot_type="humanoid",
    robot_pose=Pose(x=1.0, y=1.0),
    object_pose=Pose(x=1.2, y=1.1, z=0.8),
    detect_confidence=0.92,
)

# 查询地点
results = mem.query_place("卧室", n_results=5)
```

### 底层 IK（不对外暴露）

``utils.ik`` 中的 ``G1IKController`` / 解算器等仅供仓库内部与 ``g1_grasp_sdk`` 使用；**不再**通过 Agent Server 的 ``/code/sdk``、``/code/execute`` 或 ``from robot_sdk import ...`` 顶层导出。维护代码请使用 ``from utils.ik import ...``（工作目录需将 ``agent_server`` 加入 ``PYTHONPATH``）。

## 文件结构

```
agent_server/
├── utils/
│   ├── ik/                      # IK 解算、双臂轨迹、G1IKController
│   │   ├── g1_ik_solver.py
│   │   ├── dual_arm_controller.py
│   │   ├── g1_ik_sdk.py
│   │   └── __init__.py
│   └── models/                  # MuJoCo XML / URDF（g1_description）
├── robot_sdk/
│   ├── g1_grasp_sdk.py          # 固定轨迹抓取，对外仅 grasp_target
│   ├── navigation_sdk.py        # ROS2 导航客户端
│   ├── face_sdk.py              # 人脸识别客户端
│   ├── memory_sdk.py            # 空间记忆客户端
│   ├── robotbridge.py
│   ├── hand_sdk.py
│   ├── demo_correct.py
│   ├── demo_lift.py
│   └── README.md                # 本文档
```
