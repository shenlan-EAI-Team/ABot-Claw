---
summary: G1 Robot Plan Schema
---

# G1 Plan Schema

Plan 描述步骤和依赖；`run_plan.py` 在 lease 前完成 Memory 查询和目标解析，
再生成固定机器人端执行代码。

## 基础执行步骤

- `face_wait`：`target` 或 `any_known: true`；可选 `timeout_s` 和 `poll_interval_s`。
- `speak`：必填 `text`；执行器在首次播报前初始化 TTS。
- `detect_object`：必填 `object`；使用 D455 RGB 和 `yolo.detect_on_rgb()`，返回 `found`、`detections` 和 `matches`。
- `release`：调用预注入的 `release_object()`，返回 `released`。
- `wait`：可选非负数 `seconds`。

## grasp

字段：

- `object: string`，必填，抓取目标名称。
- `use_vlac?: boolean`，默认 `false`；只有显式设为 `true` 时启用视觉验收。
- `task_description?: string`，`use_vlac=true` 时传给 VLAC Critic 的明确任务文本；缺省时根据 `object` 生成。
- `settle_seconds?: number`，`use_vlac=true` 时的动作后画面稳定等待，默认 2 秒。

`grasp` 默认保持原有 `grasp_something()` 路径。`use_vlac=true` 时才使用
D435i 获取原始 Before/After RGB，并调用 VLAC `/critic` 和 `/grasp/verify`。
阶段结果分开返回 `execution_success`、`reward` 和 `done`。VLAC
不可达时抓取不会抛出评价异常，该阶段标记为 `partial`。
验证决策映射为 `REMOVED → success`、`STILL_PRESENT → failed`、
`UNCERTAIN → partial`。

```json
{
  "steps": [
    {
      "id": "pick_bottle",
      "type": "grasp",
      "object": "bottle",
      "use_vlac": true,
      "task_description": "Pick up the bottle from the table."
    }
  ]
}
```

## remember_visual_location

字段：

- `place_id?: string`
- `place_id_from?: string`，引用更早的 `remember_location`
- `semantic_note?: string`
- `semantic_tags?: list[string]`

同一张固定姿态 D455 JPEG 同时写入 Semantic Frame 和 VPR，VPR
`image_id` 等于 `place_id`。该步骤不调用 Navigation。

示例 1：视觉地点记忆

```json
{
  "steps": [
    {
      "id": "remember_place",
      "type": "remember_location",
      "location": "工位右侧"
    },
    {
      "id": "remember_visual",
      "type": "remember_visual_location",
      "place_id_from": "remember_place",
      "semantic_note": "这里有办公桌",
      "semantic_tags": ["办公桌"]
    }
  ]
}
```

## navigate

三种目标字段至少提供一个：

- `location?: string`
- `target_pose?: object`
- `semantic_text?: string`

语义导航可选：

- `place_match_radius_m?: number`，必须大于 0；默认复用部署配置
  `acceptance.navigation_position_tolerance_m`，当前为 `0.20 m`；配置缺失时
  回退为 `0.5 m`。

`semantic_text` 的解析发生在 lease 前：

```text
POST /query/semantic/text (memory_type=semantic_frame)
  ↓ 最佳 semantic target_pose
POST /query/position (memory_type=place)
  ↓ 半径内距离最近的 Place
place_id + Place 精确 target_pose
```

精确 pose 的 `x/y/z/qx/qy/qz/qw` 会完整传给 Navigation；没有四元数时
才回退到 `yaw`。

## vpr_verify

字段：

- `location?: string`
- `place_id?: string`
- `place_id_from?: string`，引用更早且已解析出 `place_id` 的步骤

导航成功后固定姿态拍一张图，调用一次 `vpr.search()`，比较
`matched_place_id == expected_place_id`。失败后不运动、不重试。

示例 2：物品描述导航和 VPR verify

```json
{
  "steps": [
    {
      "id": "go_to_desk",
      "type": "navigate",
      "semantic_text": "办公桌"
    },
    {
      "id": "verify_desk",
      "type": "vpr_verify",
      "place_id_from": "go_to_desk"
    }
  ]
}
```

Navigation 失败属于 fatal failure，后续 verify 记录为
`skipped / previous_fatal_failure`。
