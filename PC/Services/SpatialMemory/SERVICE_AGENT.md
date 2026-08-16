# SpatialMemory Agent 接入手册

## 1. 服务定位

SpatialMemory 是独立的统一记忆服务，面向机器人 Agent 提供四类空间记忆能力:

- 对象记忆 Object Memory
- 地点记忆 Place Memory
- 关键帧记忆 Keyframe Memory
- 语义帧记忆 Semantic Frame Memory

设计目标:

- 统一写入入口
- 统一检索模型
- 统一导航返回结构
- 在线与离线流程可并行工作

## 2. 四大类记忆能力说明

### 2.1 对象记忆 Object Memory

适用场景:

- 相机经过检测模型后，记录 cup、bottle、remote 等物体出现位置
- 支持按 object_name 检索

关键字段:

- object_name
- object_pose
- robot_id, robot_type, robot_pose
- detect_confidence
- bbox_xyxy
- image (可选)

### 2.2 地点记忆 Place Memory

适用场景:

- 机器人建图或巡视时，用户标注“这里是厨房/客厅/充电区”
- 支持按 place_name 或位置半径检索

关键字段:

- place_name
- place_pose
- alias
- robot_id, robot_type
- note

### 2.3 关键帧记忆 Keyframe Memory

适用场景:

- 离线视频关键帧批量入库
- 对巡检过程进行回放式空间记忆构建

关键字段:

- camera_source
- score, rank
- timestamp, timestamp_ns
- pose
- image

### 2.4 语义帧记忆 Semantic Frame Memory

适用场景:

- 保存图像 + 语义注释，用于文本回忆检索

关键字段:

- image
- robot_pose
- note, tags
- source, task_id

## 3. 推荐调用流程

在线流程:

1. 调用 GET /health 确认服务状态
2. 根据感知结果写入对象/地点/语义帧记忆
3. 通过 query 接口检索目标
4. 将返回中的 target_pose 下发导航模块

离线流程:

1. 调用 POST /pipeline/tasks 创建离线任务
2. 调用 GET /pipeline/tasks/{task_id} 查询进度
3. 任务完成后通过 query 接口验证记忆可检索性

## 4. API 详细说明与请求示例

### 4.1 健康检查

接口:

- GET /health

示例请求:

```bash
curl http://127.0.0.1:8022/health
```

示例响应:

```json
{
   "status": "ok",
   "service": "Unified Spatial Memory Hub",
   "version": "0.1.0",
   "records": 12,
   "data_dir": ".../SpatialMemory/data"
}
```

### 4.2 对象记忆写入/更新

接口:

- POST /memory/object/upsert

示例请求:

```json
{
   "object_name": "cup",
   "robot_id": "humanoid_001",
   "robot_type": "humanoid",
   "robot_pose": {
      "x": 1.0,
      "y": 1.0,
      "z": 0.0,
      "roll": 0.0,
      "pitch": 0.0,
      "yaw": 0.2,
      "frame_id": "map"
   },
   "object_pose": {
      "x": 1.3,
      "y": 1.1,
      "z": 0.8,
      "roll": 0.0,
      "pitch": 0.0,
      "yaw": 0.0,
      "frame_id": "map"
   },
   "bbox_xyxy": [120, 80, 240, 220],
   "detect_confidence": 0.92,
   "tags": ["kitchen", "table"],
   "note": "red cup near sink",
   "image": "<base64>"
}
```

示例响应:

```json
{
   "ok": true,
   "id": "obj_xxxxxxxxxxxx"
}
```

### 4.3 地点记忆写入/更新

接口:

- POST /memory/place/upsert

示例请求:

```json
{
   "place_name": "kitchen",
   "robot_id": "humanoid_001",
   "robot_type": "humanoid",
   "place_pose": {
      "x": 2.0,
      "y": 3.0,
      "z": 0.0,
      "roll": 0.0,
      "pitch": 0.0,
      "yaw": 1.57,
      "frame_id": "map"
   },
   "alias": ["厨房", "cooking_area"],
   "note": "main kitchen anchor"
}
```

### 4.4 语义帧记忆写入

接口:

- POST /memory/semantic/ingest

示例请求:

```json
{
   "robot_id": "dog_001",
   "robot_type": "robot_dog",
   "robot_pose": {
      "x": 2.0,
      "y": -0.5,
      "z": 0.0,
      "roll": 0.0,
      "pitch": 0.0,
      "yaw": 1.2,
      "frame_id": "map"
   },
   "source": "front_camera",
   "task_id": "patrol_20260330_01",
   "tags": ["smoke", "kitchen"],
   "note": "red cup near table",
   "image": "<base64>"
}
```

### 4.5 关键帧批量写入

接口:

- POST /memory/keyframe/ingest-batch

示例请求:

```json
{
   "task_id": "offline_patrol_001",
   "items": [
      {
         "camera_source": "front_camera",
         "rank": 1,
         "score": 0.89,
         "timestamp": 1774839093.12,
         "timestamp_ns": 1774839093120000000,
         "robot_id": "dog_001",
         "robot_type": "robot_dog",
         "pose": {
            "x": 3.0,
            "y": 1.5,
            "z": 0.0,
            "roll": 0.0,
            "pitch": 0.0,
            "yaw": 0.3,
            "frame_id": "map"
         },
         "note": "patrol keyframe",
         "image": "<base64>"
      }
   ]
}
```

### 4.6 对象检索

接口:

- POST /query/object

示例请求:

```json
{
   "name": "cup",
   "n_results": 5,
   "robot_id": "humanoid_001"
}
```

### 4.7 地点检索

接口:

- POST /query/place

示例请求:

```json
{
   "name": "kitchen",
   "n_results": 5
}
```

### 4.8 位置检索

接口:

- POST /query/position

示例请求:

```json
{
   "x": 1.0,
   "y": 1.0,
   "radius": 2.0,
   "n_results": 10,
   "memory_type": "object"
}
```

### 4.9 文本语义检索

接口:

- POST /query/semantic/text

示例请求:

```json
{
   "text": "red cup near table",
   "n_results": 5,
   "memory_type": "semantic_frame"
}
```

### 4.10 统一组合检索

接口:

- POST /query/unified

示例请求:

```json
{
   "object_name": "cup",
   "robot_id": "humanoid_001",
   "n_results": 5
}
```

### 4.11 离线任务创建与状态查询

接口:

- POST /pipeline/tasks
- GET /pipeline/tasks/{task_id}

示例创建请求:

```json
{
   "task_name": "offline_keyframe_pipeline",
   "input_uri": "file:///tmp/patrol_001.bag",
   "robot_id": "dog_001",
   "robot_type": "robot_dog",
   "options": {
      "sample_every": 15,
      "top_k": 10,
      "min_gap": 30
   }
}
```

示例状态响应:

```json
{
   "task_id": "task_xxxxxxxxxxxx",
   "task_name": "offline_keyframe_pipeline",
   "status": "running",
   "progress": 0.6,
   "result_json": "{\"stage\":\"extract_keyframes\"}"
}
```

### 4.12 视觉地点索引第一阶段

服务边界：

- SpatialMemory 地址仍为 `http://127.0.0.1:8022`。
- VPR 独立监听 `8030`。SpatialMemory 不主动调用它；跨服务步骤由上层
  OpenClaw 编排。
- SpatialMemory 只保存地点、原始参考 JPEG、图片 SHA-256 和索引状态摘要，不保存
  SALAD embedding、embedding cache 或 FAISS 数据。
- 当前 `place_id` 和 `image_id` 值相同，但概念独立，Agent 必须分别读取。

地点 upsert 请求不变，响应保留 `ok/id/has_reference_image/image_path` 并新增
`place_id`、`image_id`、`image_sha256`、`visual_index_status` 和
`visual_index`。哈希对应最终落盘的 JPEG 字节。

按稳定 ID 查询：

```bash
curl --fail \
  http://127.0.0.1:8022/memory/place/<place_id>
```

下载参考图：

```bash
curl --fail \
  http://127.0.0.1:8022/memory/place/<place_id>/image \
  --output /tmp/place.jpg
```

只更新索引状态：

```bash
curl --fail -X PATCH \
  http://127.0.0.1:8022/memory/place/<place_id>/visual-index \
  -H 'Content-Type: application/json' \
  -d '{
    "status": "indexed",
    "image_id": "<image_id>",
    "image_sha256": "<upsert 返回的 SHA-256>",
    "backend": "salad",
    "version": "salad_v1"
  }'
```

合法状态为 `not_indexed`、`pending`、`indexed`、`failed`、`deleted`。
`indexed` 必须同时提供 `backend` 与 `version`；哈希与当前参考图不匹配返回
`409`；`updated_at` 由 SpatialMemory 生成。PATCH 只允许修改
`extra.visual_index`，不会修改名称、别名、pose、备注或图片路径。

老地点没有视觉索引元数据时自动返回 `not_indexed`；存在图片时按需计算
SHA-256，图片缺失时返回 `null` 而不是使地点查询失败。`POST /query/place` 的
`id`、`target_pose`、`evidence.image_path` 保持兼容，并增加相同的地点图片和索引
元数据。

下一阶段 Agent 编排契约：

1. `POST /memory/place/upsert`，保存 `place_id/image_id/image_sha256`。
2. `PATCH /memory/place/{place_id}/visual-index`，写入 `pending`。
3. 调用 `POST http://127.0.0.1:8030/visual-index/images/upload`。
4. 根据 VPR 结果 PATCH 为 `indexed` 或 `failed`。

## 5. 统一检索返回结构

所有 query 返回均遵循统一结构，建议 Agent 只依赖该结构的关键字段，不要耦合内部实现细节。

关键字段:

- memory_type
- target_pose
- confidence
- evidence

示例:

```json
{
   "results": [
      {
         "id": "obj_33c65ebe1329",
         "memory_type": "object",
         "name": "cup",
         "robot_id": "humanoid_001",
         "robot_type": "humanoid",
         "target_pose": {
            "x": 1.2,
            "y": 1.1,
            "z": 0.8,
            "roll": 0.0,
            "pitch": 0.0,
            "yaw": 0.0,
            "qx": null,
            "qy": null,
            "qz": null,
            "qw": null,
            "frame_id": "map"
         },
         "confidence": 0.92,
         "evidence": {
            "image_path": ".../images/obj_33c65ebe1329.jpg",
            "note": "red cup near sink",
            "extra": {
               "bbox_xyxy": [120, 80, 240, 220]
            }
         }
      }
   ]
}
```

## 6. Agent 调用模板

以下模板可直接用于 Agent 逻辑编排。

### 模板 A: 找特定物体并导航

步骤:

1. POST /query/object with name
2. 若有结果，取 results[0].target_pose
3. 调用导航模块 move_to(target_pose)

请求模板:

```json
{
   "name": "<object_name>",
   "n_results": 3,
   "robot_id": "<robot_id_optional>"
}
```

### 模板 B: 用户标注地点并存档

步骤:

1. 获取当前机器人位姿
2. POST /memory/place/upsert
3. 返回地点 id 给上层任务

请求模板:

```json
{
   "place_name": "<place_name>",
   "robot_id": "<robot_id>",
   "robot_type": "<robot_type>",
   "place_pose": {
      "x": 0.0,
      "y": 0.0,
      "z": 0.0,
      "roll": 0.0,
      "pitch": 0.0,
      "yaw": 0.0,
      "frame_id": "map"
   },
   "alias": [],
   "note": ""
}
```

### 模板 C: 在线语义回忆

步骤:

1. POST /query/semantic/text
2. 读取 top-k 结果
3. 若需导航，使用 target_pose

请求模板:

```json
{
   "text": "<natural_language_query>",
   "n_results": 5,
   "memory_type": "semantic_frame"
}
```

### 模板 D: 发起离线巡检记忆任务

步骤:

1. POST /pipeline/tasks 创建任务
2. 轮询 GET /pipeline/tasks/{task_id}
3. 状态 completed 后执行 query 验证

创建请求模板:

```json
{
   "task_name": "offline_keyframe_pipeline",
   "input_uri": "<bag_or_video_uri>",
   "robot_id": "<robot_id>",
   "robot_type": "<robot_type>",
   "options": {
      "sample_every": 15,
      "top_k": 10,
      "min_gap": 30
   }
}
```

## 7. 错误处理建议

- 4xx 错误: 认为请求体问题，修正参数后重试
- 5xx 错误: 可进行指数退避重试
- 查询空结果: 不视为异常，返回“未找到”并可降级到统一检索
- 任务状态 failed: 读取 error 字段并记录 options 复现
