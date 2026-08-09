# GraspAnything

GraspAnything 是 ABot-Claw 的独立 HTTP 抓取检测服务，和 `Services/YOLO`、`Services/SpatialMemory` 一样通过 `main.py` 暴露 FastAPI 接口。

它不维护另一份 AnyGrasp SDK；服务运行时通过环境变量加载已经安装好的 AnyGrasp SDK。

## 默认端口

- `8015`
- 健康检查：`GET /health`
- 抓取检测：`POST /grasp/detect`

## 统一启动

推荐从 `Services` 根目录统一启动：

```bash
cd /home/slxy/szm1/ABot-Claw/Services
./start_services.sh
```

`start_services.sh` 默认使用：

```bash
ANYGRASP_SDK_PATH=/home/slxy/anygrasp_sdk
GRASP_CHECKPOINT_PATH=/home/slxy/anygrasp_sdk/grasp_detection/log/checkpoint_detection.tar
ANYGRASP_PYTHON=/home/slxy/miniconda3/envs/anygrasp/bin/python
```

需要切换路径时，在启动前覆盖环境变量即可。

## 单独启动

```bash
cd /home/slxy/szm1/ABot-Claw/Services/GraspAnything
PORT=8015 \
DEVICE=auto \
ANYGRASP_SDK_PATH=/home/slxy/anygrasp_sdk \
GRASP_CHECKPOINT_PATH=/home/slxy/anygrasp_sdk/grasp_detection/log/checkpoint_detection.tar \
/home/slxy/miniconda3/envs/anygrasp/bin/python main.py
```

## 请求格式

```json
{
  "color_image": "<base64_png_or_jpeg>",
  "depth_image": "<base64_uint16_depth_png>",
  "camera_intrinsics": [[600, 0, 320], [0, 600, 240], [0, 0, 1]],
  "object_name": "cup",
  "top_k": 5
}
```

## 返回格式

返回相机坐标系下的抓取候选：

```json
{
  "frame_id": "camera_frame",
  "target": "cup",
  "top_k": 5,
  "count": 1,
  "results": [
    {
      "label": "cup",
      "confidence": 0.91,
      "xyxy": [120, 80, 260, 290],
      "grasps": [
        {
          "score": 0.66,
          "width": 0.05,
          "translation_camera": [0.11, 0.03, 0.42],
          "translation_camera_retreat": [0.11, 0.03, 0.52],
          "quaternion_camera_xyzw": [0.0, 0.7, 0.0, 0.7],
          "rotation_camera": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        }
      ]
    }
  ],
  "latency_ms": 185.3
}
```
