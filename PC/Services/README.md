# Services 启动说明

这个目录下的服务是彼此独立的 HTTP 后端，默认不是统一编排启动，而是按目录分别运行。

当前推荐的统一启动方式是使用根目录脚本：

```bash
cd /home/slxy/szm1/ABot-Claw/Services
chmod +x start_services.sh
./start_services.sh
```

脚本默认会启动以下服务：

- `YOLO`，端口 `8013`
- `GraspAnything`，端口 `8015`
- `face_recognition_insightface`，端口 `8016`
- `SpatialMemory`，端口 `8022`

脚本当前不会启动：

- `VLAC`

原因：

- `VLAC` 依赖较重，适合确认模型和环境后再单独启动
- `GraspAnything` 使用独立的 `anygrasp` Python 环境；默认读取 `/home/slxy/anygrasp_sdk`，也可以通过环境变量覆盖

## 一键启动脚本行为

`start_services.sh` 会做这些事：

1. 检查目标端口是否已经被占用
2. 如果服务未运行，则进入对应目录后台启动
3. 将日志写入 `Services/logs/`
4. 将启动进程号写入 `Services/.pids/`
5. 轮询 `/health` 接口，输出是否启动成功

## 日志位置

启动后可查看：

- `Services/logs/yolo.log`
- `Services/logs/graspanything.log`
- `Services/logs/face_recognition.log`
- `Services/logs/spatial_memory.log`

## 当前默认端口

| 服务 | 端口 | 健康检查 |
|---|---:|---|
| YOLO | `8013` | `GET /health` |
| VLAC | `8014` | `GET /health` |
| GraspAnything | `8015` | `GET /health` |
| face_recognition_insightface | `8016` | `GET /health` |
| SpatialMemory | `8022` | `GET /health` |

## 单独启动

如果你想单独启动某个服务，可以分别执行：

### YOLO

```bash
cd /home/slxy/szm1/ABot-Claw/Services/YOLO
pip install -r requirements.txt
PORT=8013 python main.py
```

### VLAC

```bash
cd /home/slxy/szm1/ABot-Claw/Services/VLAC
pip install -r requirements.txt
PORT=8014 python main.py
```

### face_recognition_insightface

推荐使用已有 GPU 启动脚本：

```bash
cd /home/slxy/szm1/ABot-Claw/Services/face_recognition_insightface
PORT=8016 DEVICE=auto ./run_service_gpu.sh
```

### SpatialMemory

```bash
cd /home/slxy/szm1/ABot-Claw/Services/SpatialMemory
pip install -r requirements.txt
PORT=8022 python main.py
```

### GraspAnything

GraspAnything 与其他服务一样提供 `GET /health` 和 HTTP 接口，但使用已安装好的 AnyGrasp SDK 环境。默认配置：

- `ANYGRASP_SDK_PATH=/home/slxy/anygrasp_sdk`
- `GRASP_CHECKPOINT_PATH=$ANYGRASP_SDK_PATH/grasp_detection/log/checkpoint_detection.tar`
- `ANYGRASP_PYTHON=/home/slxy/miniconda3/envs/anygrasp/bin/python`

单独启动：

```bash
cd /home/slxy/szm1/ABot-Claw/Services/GraspAnything
PORT=8015 \
ANYGRASP_SDK_PATH=/home/slxy/anygrasp_sdk \
GRASP_CHECKPOINT_PATH=/home/slxy/anygrasp_sdk/grasp_detection/log/checkpoint_detection.tar \
/home/slxy/miniconda3/envs/anygrasp/bin/python main.py
```



for f in /home/slxy/szm1/ABot-Claw/Services/.pids/*.pid; do [ -f "$f" ] && kill $(cat "$f") 2>/dev/null; done
