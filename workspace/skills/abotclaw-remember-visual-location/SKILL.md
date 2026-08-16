---
name: abotclaw-remember-visual-location
description: Store one fixed-pose D455 image in Semantic Memory and VPR.
---

# Remember Visual Location

为 SpatialMemory 已创建的 `place_id` 建立单张视觉记忆。机器人必须保持在
该 Place 的固定、精确 pose；本 Skill 不调用 Navigation。

输入：

- `place_id`：必需，已有 Place ID；
- `semantic_note`：可选，例如“这里有办公桌”；
- `semantic_tags`：可选字符串列表，例如 `["办公桌"]`。

```text
已有 place_id + target_pose
  ↓
D455 get_frame 一次
  ↓
保存一张 JPEG
  ├─ SpatialMemory Semantic Frame ingest（图片 embedding + pose + note/tags）
  └─ VPR upload_image（image_id = place_id）
  ↓
SpatialMemory visual_index = indexed
```

返回至少包含：

```json
{
  "place_id": "plc_xxxxx",
  "semantic_memory_id": "mem_xxxxx",
  "image_id": "plc_xxxxx",
  "visual_index": {
    "status": "indexed",
    "image_id": "plc_xxxxx",
    "backend": "salad",
    "version": "salad_v1"
  }
}
```

约束：只拍一张图；Semantic Memory 和 VPR 必须复用同一 JPEG；不得旋转、
移动、探索、重复扫描或创建第二个 Place。
