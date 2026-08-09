# Face Recognition Service（Agent 详细接入手册）

## 1. 服务作用与能力边界

Face Recognition 服务用于对单张图片执行人脸识别，并管理一个轻量级人脸特征库。

可实现能力：

- 查询当前已录入人员
- 单人录入
- 批量录入
- 单张图片识别人脸并返回匹配结果

非目标：

- 不处理视频流
- 不做跨帧 tracking
- 不做 ROS / ZMQ 图像流订阅
- 不做人脸属性分析

默认端口：`8016`

## 2. 启动与运行参数

```bash
cd /home/slxy/ABot-Claw/Services/face_recognition_insightface
./run_service_gpu.sh
```

关键环境变量：

- `PORT`：监听端口，默认 `8016`
- `DEVICE`：统一设备参数，支持 `auto | cpu | cuda | 0 | -1`
- `FACE_RECOGNITION_CTX_ID`：兼容旧参数，`DEVICE` 优先
- `FACE_RECOGNITION_MODEL`：模型名，默认 `buffalo_l`
- `FACE_RECOGNITION_DET_SIZE`：检测尺寸，默认 `640`
- `FACE_DB_PATH`：人脸库 JSON 路径

启动说明：

- `run_service_gpu.sh` 使用本目录下的 `.venv`（符号链接到原始 venv）
- 该脚本会注入 CUDA 所需的 `LD_LIBRARY_PATH`
- 若直接用 `python main.py` 启动，可能因为缺少 CUDA 动态库路径而回退到 CPU

## 3. 图像输入规范（非常重要）

本服务中所有图像字段都支持：

1. raw base64（推荐）
2. data URI（`data:image/png;base64,...`）
3. 本地路径
4. URL（http/https）

建议 Agent 一律发送 raw base64，避免服务端路径依赖。

## 4. 接口说明

### `GET /health`

用途：探活与模型加载状态确认。

典型返回字段：

- `status`
- `version`
- `ctx_id`
- `model_name`
- `det_size`
- `db_path`
- `people_count`
- `model_loaded`

### `GET /face/people`

用途：读取当前人脸库中的人员列表。

返回字段：

- `count`
- `people`

### `POST /face/enroll`

用途：录入单个人员。

请求体：

```json
{
  "name": "zhangsan",
  "images": ["<base64_or_path_or_url>"]
}
```

说明：

- 每张图只使用面积最大的那张脸
- 会对多张图的人脸 embedding 做归一化平均
- 若所有图片都未检测到人脸，则返回 400

返回体：

```json
{
  "name": "zhangsan",
  "samples_received": 3,
  "samples_used": 2,
  "total_people": 5,
  "db_path": "/abs/path/to/face_db.json"
}
```

### `POST /face/enroll/batch`

用途：批量录入多个人员。

请求体：

```json
{
  "people": [
    {
      "name": "zhangsan",
      "images": ["<base64_1>", "<base64_2>"]
    },
    {
      "name": "lisi",
      "images": ["<base64_3>"]
    }
  ]
}
```

### `POST /face/recognize`

用途：识别单张图片中的所有人脸。

请求体：

```json
{
  "image": "<base64_or_path_or_url>",
  "threshold": 0.45,
  "include_annotated_image": false
}
```

参数说明：

- `threshold`：余弦相似度阈值，默认 `0.45`
- `include_annotated_image`：若为 `true`，额外返回标注后图片的 base64 JPEG

返回体：

```json
{
  "count": 2,
  "threshold": 0.45,
  "results": [
    {
      "bbox": [120, 80, 240, 220],
      "name": "zhangsan",
      "match_score": 0.82,
      "det_score": 0.99
    }
  ],
  "latency_ms": 68.5,
  "annotated_image": null
}
```

## 5. Agent 推荐调用流程

1. 先调用 `GET /health`，确认 `model_loaded=true`
2. 若人脸库为空，先调用 `POST /face/enroll` 或 `POST /face/enroll/batch`
3. 再调用 `POST /face/recognize`
4. 如需可视化结果，可在请求中设置 `include_annotated_image=true`

## 6. 常见错误与处理

- `400 name cannot be empty`
- `400 Invalid image payload`
- `400 No face detected from provided images`
- `503 Face model not initialized`
- `500 Recognition failed`

排障建议：

- 优先使用清晰、正脸、无遮挡的人脸图片录入
- 同一个人尽量提供多张不同角度图片
- 若误识别较多，可适当提高 `threshold`
