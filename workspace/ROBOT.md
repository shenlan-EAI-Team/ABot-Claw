# ROBOT.md — Unitree G1 Stable Facts

本项目只有一台机器人：

- Robot ID：`g1_001`
- 类型：Unitree G1 humanoid
- Robot Agent Server：见 `config/deployment.yaml`
- ROS：ROS1

## 1. 能力边界

| 能力 | 已验证入口 |
|---|---|
| 健康与本体状态 | `GET /health`、`GET /state` |
| 当前 map 位姿 | `GET /nav/current_pose`；导航执行中可用 `Nav2Anywhere.get_current_pose()` |
| 地点/对象记忆 | Spatial Memory；由 `memory`、`remember-location` Skill 管理 |
| 导航 | `/code/execute` 内 `Nav2Anywhere` |
| 人脸识别 | 预注入 `face.recognize_current_frame()` |
| TTS | `tts.initialize()` 后 `tts.speak(text)` |
| 前向检测 | D455：`camera.get_frame()` + `yolo.detect_on_rgb(rgb)` |
| 抓取检测 | D435i：`yolo.detect_env()` / `grasp_something(name)` |
| 抓取并视觉验收 | `grasp_with_vlac(name, task_description=...)` |
| 释放 | `release_object()` 或 `release_something()` |
| 视觉地点识别 | VPR：`vpr.search(image_path)` |

不在此表中的方法视为未知，才调用 `sdk-discovery`。

## 2. 相机绑定

- `camera` / D455：水平朝前；人脸、人数、场景、非抓取目标检测。
- `camera_d435i` / D435i：向下；只用于抓取相关视觉。
- 禁止用 `yolo.detect_env()` 判断站立人员。

## 3. 读操作与物理执行

### 无 lease

- `/health`
- `/state`
- `/nav/current_pose`
- Memory HTTP 查询/写入
- SDK 和 OpenAPI 只读发现

### 需要 lease

凡是通过 `/code/execute` 执行导航、TTS、人脸等待、手部动作、抓取或组合流程：

```text
POST /lease/acquire
→ POST /code/execute（X-Lease-Id header）
→ GET /code/result/{execution_id}
→ POST /lease/release
```

申请 lease 前必须完成 plan 校验和代码编译；禁止边规划边持有 lease。

## 4. `/code/execute` 预注入对象

- `Nav2Anywhere`
- `face`
- `tts`
- `camera`
- `camera_d435i`
- `yolo`
- `memory`
- `vpr`
- `vlac`
- `env`
- `Pose`
- `grasp_something(name)`
- `grasp_with_vlac(name, task_description=...)`：共享 D435i Before/After，返回 `execution_success`、`reward`、`done`
- `grasp_target(...)`
- `release_object()`
- `release_something()`

## 5. 导航验收

默认：

- 位置误差 ≤ `0.20 m`
- 朝向误差 ≤ `0.175 rad`

阈值由 `config/deployment.yaml` 管理。

## 6. 机器人端代码约束

- 使用固定模板和结构化 plan，不让模型现场自由拼接完整程序。
- 允许标准库 `json`、`time`、`math` 和 ROS 消息类型。
- 禁止网络、子进程和越权 import。
- `tts.speak` 前必须初始化。
- `Nav2Anywhere` 内部负责 ROS 节点初始化，不重复 `rospy.init_node()`。
