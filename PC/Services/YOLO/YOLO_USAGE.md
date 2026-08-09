# YOLO 目标检测服务

基于 `FastAPI + YOLOv5l6` 的目标检测 HTTP 服务。接收图片输入，返回检测到的目标边界框、置信度和类别。

---

## 目录结构

```
YOLO/
  main.py              # FastAPI 服务入口
  detect.py            # YOLOv5 命令行本地检测脚本
  yolov5l6.pt          # YOLOv5l6 模型权重
  requirements.txt     # Python 依赖
  data/                # YOLOv5 默认数据目录
    images/            # 检测用图片目录
    labels/            # 标签目录
```

---

## 启动方式

### 方式一：通过 start_services.sh 启动（推荐）

```bash
cd /home/xxuz/Services
./start_services.sh
```

YOLO 默认监听端口 `8013`。

### 方式二：单独启动

```bash
cd /home/xxuz/Services/YOLO
PORT=8013 DEVICE=auto .venv/bin/python main.py
```

---

## 环境变量


| 变量                | 默认值                | 说明                                      |
| ----------------- | ------------------ | --------------------------------------- |
| `PORT`            | `8013`             | HTTP 监听端口                               |
| `DEVICE`          | `auto`             | 设备类型：`auto` / `cpu` / `cuda` / `cuda:0` |
| `YOLO_DEVICE`     | —                  | 兼容旧参数（`DEVICE` 优先）                      |
| `YOLO_MODEL_PATH` | `YOLO/yolov5l6.pt` | 模型权重路径                                  |


---

## API 接口

### `GET /health`

探活接口，确认模型加载状态。

```bash
curl http://127.0.0.1:8013/health
```

**响应示例：**

```json
{
  "status": "ok",
  "version": "0.1.0",
  "device": "cuda:0",
  "model_path": "/home/xxuz/Services/YOLO/yolov5l6.pt",
  "model_loaded": true
}
```

---

### `POST /detect`

目标检测接口。

**请求体：**

```json
{
  "image": "<base64_or_path_or_url>",
  "conf_thres": 0.25,
  "iou_thres": 0.45
}
```

**参数说明：**


| 参数           | 类型     | 默认值    | 说明               |
| ------------ | ------ | ------ | ---------------- |
| `image`      | string | 必填     | 图片（见下方输入格式）      |
| `conf_thres` | float  | `0.25` | 置信度阈值，高于此值才返回    |
| `iou_thres`  | float  | `0.45` | NMS IoU 阈值，去除重叠框 |


**响应示例：**

```json
{
  "count": 2,
  "detections": [
    {
      "x1": 120.0,
      "y1": 80.0,
      "x2": 340.0,
      "y2": 480.0,
      "confidence": 0.92,
      "class_id": 0,
      "class_name": "cup"
    },
    {
      "x1": 400.0,
      "y1": 60.0,
      "x2": 700.0,
      "y2": 420.0,
      "confidence": 0.88,
      "class_id": 40,
      "class_name": "bottle"
    }
  ]
}
```

**字段说明：**


| 字段           | 说明                              |
| ------------ | ------------------------------- |
| `x1, y1`     | 边界框左上角坐标                        |
| `x2, y2`     | 边界框右下角坐标                        |
| `confidence` | 检测置信度（0~1）                      |
| `class_id`   | 类别数字 ID                         |
| `class_name` | 类别名称（如 `cup`、`bottle`、`orange`） |


---

## API 调用命令

### 1. 健康检查

```bash
curl http://127.0.0.1:8013/health
```

---

### 2. 检测（本地图片文件）

```bash
# 将图片转为 base64 后发送
curl -X POST http://127.0.0.1:8013/detect \
  -H 'Content-Type: application/json' \
  -d '{
    "image": "'"$(base64 -w 0 /path/to/image.jpg)"'",
    "conf_thres": 0.25,
    "iou_thres": 0.45
  }'
```

---

### 3. 检测（直接传本地路径）

```bash
curl -X POST http://127.0.0.1:8013/detect \
  -H 'Content-Type: application/json' \
  -d '{
    "image": "/path/to/image.jpg",
    "conf_thres": 0.25,
    "iou_thres": 0.45
  }'
```

---

### 4. 检测（HTTP/HTTPS URL）

```bash
curl -X POST http://127.0.0.1:8013/detect \
  -H 'Content-Type: application/json' \
  -d '{
    "image": "https://example.com/image.jpg",
    "conf_thres": 0.25,
    "iou_thres": 0.45
  }'
```

---

### 5. 高置信度检测（严格模式）

```bash
curl -X POST http://127.0.0.1:8013/detect \
  -H 'Content-Type: application/json' \
  -d '{
    "image": "'"$(base64 -w 0 /path/to/image.jpg)"'",
    "conf_thres": 0.7,
    "iou_thres": 0.45
  }'
```

---

### 6. Python 测试脚本

```python
import base64
import requests
from pathlib import Path

URL = "http://127.0.0.1:8013/detect"
TEST_IMAGE = "test.jpg"

image_b64 = base64.b64encode(Path(TEST_IMAGE).read_bytes()).decode("utf-8")

payload = {
    "image": image_b64,
    "conf_thres": 0.25,
    "iou_thres": 0.45,
}

response = requests.post(URL, json=payload, timeout=30)
response.raise_for_status()
print(response.json())
```

运行：

```bash
cd /home/xxuz/Services/YOLO
.venv/bin/python your_test_script.py
```

---

## 命令行本地检测

不需要启动服务，直接用 YOLOv5 做本地检测（结果保存在 `runs/detect/`）：

```bash
cd /home/xxuz/Services/YOLO
.venv/bin/python detect.py --weights yolov5l6.pt --source data/images --img-size 640 --conf-thres 0.25

# 指定单张图片
.venv/bin/python detect.py --weights yolov5l6.pt --source /path/to/image.jpg

# 使用摄像头
.venv/bin/python detect.py --weights yolov5l6.pt --source 0

# 指定输出目录
.venv/bin/python detect.py --weights yolov5l6.pt --source /path/to/image.jpg --project runs/detect --name result
```

---

## 图片输入格式

所有图片字段均支持以下 4 种格式：


| 格式             | 示例                                 |
| -------------- | ---------------------------------- |
| 原始 base64      | `iVBORw0KGgoAAAANSUhEUgAAAAE...`   |
| Data URI       | `data:image/png;base64,iVBORw0...` |
| 本地路径           | `/home/xxuz/photo.jpg`             |
| HTTP/HTTPS URL | `https://example.com/photo.jpg`    |


---

## 常见错误与处理


| 状态码   | 含义                      | 解决方法                         |
| ----- | ----------------------- | ---------------------------- |
| `400` | `Invalid image payload` | 图片格式错误或 base64 解码失败          |
| `503` | `Model not initialized` | 服务启动中，等待 `model_loaded=true` |


**注意：** 如果 `model_loaded=false` 但服务已启动，可能是模型正在下载中（首次启动时会从 Ultralytics 下载）。可查看日志确认进度：

```bash
tail -f /home/xxuz/Services/logs/yolo.log
```

