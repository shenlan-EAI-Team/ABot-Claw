# Services

机器人服务集合，包含多个独立的 HTTP 后端服务，默认通过根目录脚本统一启动。

## 一、快速启动

```bash
cd /home/xxuz/Services
chmod +x start_services.sh
./start_services.sh
```

## 二、服务列表

| 服务 | 端口 | 说明 |
|------|------|------|
| YOLO | 8013 | 目标检测 |
| VLAC | 8014 | 视觉语言动作模型（默认不自动启动） |
| GraspAnything | 8015 | 抓取规划 |
| face_recognition_insightface | 8016 | InsightFace 人脸识别 |
| SpatialMemory | 8022 | 空间记忆（对象、地点、关键帧、语义帧） |

## 三、单独启动

### YOLO

```bash
cd /home/xxuz/Services/YOLO
pip install -r requirements.txt
PORT=8013 python main.py
```

### VLAC

```bash
cd /home/xxuz/Services/VLAC
pip install -r requirements.txt
PORT=8014 python main.py
```

### face_recognition_insightface

推荐使用 GPU 启动脚本：

```bash
cd /home/xxuz/Services/face_recognition_insightface
./run_service_gpu.sh
```

或手动指定：

```bash
cd /home/xxuz/Services/face_recognition_insightface
PORT=8016 DEVICE=auto .venv/bin/python main.py
```

常用环境变量：

- `PORT`：监听端口，默认 `8016`
- `DEVICE`：设备类型，`auto | cpu | cuda | 0 | -1`
- `FACE_DB_PATH`：人脸库 JSON 路径

### SpatialMemory

```bash
cd /home/xxuz/Services/SpatialMemory
pip install -r requirements.txt
PORT=8022 python main.py
```

### GraspAnything

```bash
cd /home/xxuz/Services/GraspAnything
PORT=8015 \
  ANYGRASP_SDK_PATH=/home/xxuz/anygrasp_sdk \
  GRASP_CHECKPOINT_PATH=/home/xxuz/anygrasp_sdk/grasp_detection/log/checkpoint_detection.tar \
  /home/xxuz/miniconda3/envs/anygrasp/bin/python main.py
```

## 四、启动脚本行为

`start_services.sh` 会：

1. 检查目标端口是否被占用
2. 未运行时进入对应目录后台启动
3. 日志写入 `Services/logs/`
4. 进程号写入 `Services/.pids/`
5. 轮询 `/health` 接口确认启动成功

## 五、日志位置

```
Services/logs/yolo.log
Services/logs/graspanything.log
Services/logs/face_recognition.log
Services/logs/spatial_memory.log
```

## 六、各服务详细文档

- [YOLO](./YOLO/)
- [VLAC](./VLAC/)
- [GraspAnything](./GraspAnything/)
- [face_recognition_insightface](./face_recognition_insightface/)
- [SpatialMemory](./SpatialMemory/)

## 七、停止服务

```bash
# 方式一：使用脚本
./stop_services.sh

# 方式二：手动 kill
for f in /home/xxuz/Services/.pids/*.pid; do [ -f "$f" ] && kill $(cat "$f") 2>/dev/null; done
```

---

## 许可证 / License

本项目采用 **GNU General Public License v3.0 (GPL-3.0)** 开源协议。

**您可以自由地：**
- 自由使用、修改、分发本软件
- 将其用于私有或商业项目内部

**您必须：**
- 在分发衍生作品时，公开源代码
- 在所有副本中保留版权声明和 GPL-3.0 许可证全文

**您不能：**
- 将本项目（包括其衍生作品）用于商业产品或服务对外销售
- 在闭源项目中使用 GPL-3.0 代码
- 在不遵守 GPL-3.0 条款的情况下分发本软件

详细协议内容请参阅 [GNU GPL v3.0 官方原文](https://www.gnu.org/licenses/gpl-3.0.txt)。

---

## 三方依赖许可证

本项目依赖以下开源组件，谨此致谢：

| 依赖 | 许可证 |
|------|--------|
| FastAPI | MIT |
| InsightFace (buffalo_l) | Apache-2.0 |
| YOLO / Ultralytics | AGPL-3.0 |
| GraspAnything / AnyGrasp | 仅供研究使用 |
| OpenCV | Apache-2.0 |
| NumPy | BSD-3-Clause |
| SQLite (Python stdlib) | PSF |
| Uvicorn | BSD-3-Clause |
