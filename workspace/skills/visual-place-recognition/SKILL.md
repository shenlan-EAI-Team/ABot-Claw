# Visual Place Recognition Skill

## Responsibility split

```text
SpatialMemory Semantic Frame:
text → historical visual memory → pose

SpatialMemory Place:
semantic pose → nearby Place → place_id + precise target_pose

Navigation:
target_pose → robot movement

VPR:
reference image ↔ place_id
current image → matched_place_id
```

VPRSDK 只提供：

```python
vpr.health()
vpr.upload_image(place_id, image_path, image_id=None)
vpr.search(image_path)
```

VPR 不执行文本搜索。物品描述查询使用 SpatialMemory
`/query/semantic/text`，随后用 `/query/position` 找到正式 Place。

## Fixed-pose indexing

一张固定姿态 D455 JPEG 同时用于 Semantic Frame ingest 和
`vpr.upload_image()`；VPR 的 `image_id` 等于 `place_id`。VPR 不创建 Place，
不生成 pose，也不控制 Navigation。

## Post-navigation verification

```text
D455 get_frame 一次
  ↓
vpr.search(image_path) 一次
  ↓
matched_place_id == expected_place_id
```

不匹配时直接失败。禁止旋转、移动、探索、局部搜索、重复拍照或重复 VPR。
