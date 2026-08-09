# InsightFace 人脸识别服务

这个目录现在已经按仓库里其他服务的形式整理成独立 HTTP 服务，基于 `FastAPI + InsightFace`，提供：

- 人脸库人员列表查询
- 单人录入
- 批量录入
- 单张图片人脸识别

## 目录说明

- `main.py`：服务入口
- `face_db.py`：人脸特征数据库读写与相似度匹配
- `requirements.txt`：服务依赖
- `service.yaml`：服务元信息
- `SERVICE_AGENT.md`：Agent 接入说明
- `data/face_db.json`：默认人脸数据库

## 启动方式

推荐使用本目录下的服务启动脚本，它会使用本目录的 `.venv`（符号链接），并注入 CUDA 运行库路径。

```bash
cd /home/slxy/gmg1/ABot-Claw/Services/face_recognition_insightface
./run_service_gpu.sh
```

如果只想手动指定解释器，也可以：

```bash
cd /home/slxy/gmg1/ABot-Claw/Services/face_recognition_insightface
PORT=8016 DEVICE=auto .venv/bin/python main.py
```

常用环境变量：

- `PORT`：监听端口，默认 `8016`
- `DEVICE`：统一设备参数，支持 `auto | cpu | cuda | 0 | -1`
- `FACE_RECOGNITION_CTX_ID`：兼容旧式 `ctx_id` 指定，`DEVICE` 优先
- `FACE_RECOGNITION_MODEL`：InsightFace 模型名，默认 `buffalo_l`
- `FACE_RECOGNITION_DET_SIZE`：检测输入尺寸，默认 `640`
- `FACE_DB_PATH`：人脸库 JSON 路径

说明：

- `run_service_gpu.sh` 会注入 CUDA 相关 `LD_LIBRARY_PATH`
- `DEVICE=auto` 时，服务会优先尝试 GPU，否则回退 CPU
- 第一次启动时，`InsightFace` 可能会自动下载模型

## 接口概览

### `GET /health`

返回服务状态、模型配置、人脸库路径和已录入人数。

### `GET /face/people`

返回当前人脸库中的人员列表。

### `POST /face/enroll`

录入单个人员。

请求体示例：

```json
{
  "name": "zhangsan",
  "images": ["<base64_or_path_or_url>"]
}
```

### `POST /face/enroll/batch`

批量录入多个人员。

请求体示例：

```json
{
  "people": [
    {
      "name": "zhangsan",
      "images": ["<base64_or_path_or_url>"]
    },
    {
      "name": "lisi",
      "images": ["<base64_or_path_or_url>"]
    }
  ]
}
```

### `POST /face/recognize`

对单张图片做人脸识别。

请求体示例：

```json
{
  "image": "<base64_or_path_or_url>",
  "threshold": 0.45,
  "include_annotated_image": false
}
```

返回结果里会包含：

- `bbox`
- `name`
- `match_score`
- `det_score`
- `latency_ms`

## 最小调用示例

```bash
curl -X POST http://127.0.0.1:8016/face/recognize \
  -H 'Content-Type: application/json' \
  -d '{"image":"<base64_image>","threshold":0.45}'
```

## 实时可视化机器人摄像头

如果你已经启动了：

- 人脸识别服务 `8016`
- G1 agent server `8001`

可以直接运行本目录下的实时预览脚本：

```bash
cd /home/slxy/gmg1/ABot-Claw/Services/face_recognition_insightface
python realtime_view.py
```

默认会循环读取 `http://127.0.0.1:8002/camera/rgb.jpg`，再调用
`http://127.0.0.1:8016/face/recognize`，弹出 OpenCV 窗口显示带框和名字的实时识别结果。

常用参数：

```bash
python realtime_view.py --fps 8 --threshold 0.5
python realtime_view.py --camera-url http://127.0.0.1:8002/camera/rgb.jpg --face-url http://127.0.0.1:8016
```

退出方式：

- 按 `q`
- 或按 `Esc`
