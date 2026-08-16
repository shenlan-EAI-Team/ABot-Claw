---
summary: G1 单机器人任务路由、编排和验收规则
read_when:
  - Every session
---

# MISSION.md — G1 Mission

## 1. 目标

在不改变原版已跑通执行机制的前提下，让 G1 任务：

- 路由清楚；
- 单能力直接执行；
- 多能力统一编排；
- 失败可定位；
- 不因重复读文档、重复发现 SDK、现场改代码而超时。

## 2. 任务分类

### A. 只读查询

例如健康、状态、位姿、服务在线情况。

- 不申请 lease。
- 使用 `robot-connection`、`active-services` 或短 HTTP 请求。

### B. 空间记忆

地点记忆任务统一由 `remember-location` 和
`remember-visual-location` 协同完成。建库时机器人必须已经处于要保存的
固定、精确导航 pose。


当用户要求：

- 记住当前位置；
- 保存这个地点；
- 以后还能找到这里；


执行：

1. `remember-location`

负责：

- 获取当前位置 pose
- 创建 place_id
- 保存空间位置


2. `remember-visual-location`

负责：

- 在当前固定 pose 使用 D455 采集单张 RGB 图片
- 将同一张 JPEG 写入 SpatialMemory Semantic Frame，保存图片 embedding、pose、note 和 tags
- VPR 建立参考图片与 place_id 的视觉索引
- 绑定 place_id
- `remember-visual-location` 必须使用 `remember-location` 创建的 place_id，不能重复创建地点。
- VPR 建库不得调用 Navigation，不得改变机器人位置或朝向。

最终地点记忆包含：

- 地点名称
- pose
- visual_index
- semantic visual memory


查询地点或对象：
- 使用 `memory`。
- 不把 Memory 的完整操作流程写进 `ROBOT.md`。

### C. 语义视觉定位与 VPR

三层职责严格分离：

1. SpatialMemory Semantic Frame：物品文本描述 → 历史视觉场景 → pose。
2. Navigation：精确 target_pose → 机器人实际移动。
3. VPR：当前单张图片 → matched_place_id，用于抵达后的视觉地点确认。

物品描述导航在 lease 前解析：

```text
semantic_text
  ↓ SpatialMemory /query/semantic/text
semantic target_pose
  ↓ SpatialMemory /query/position
附近距离最近的 Place
  ↓
expected_place_id + Place 精确 target_pose
```


要求：

- VPR只返回place_id
- 地点信息由Memory提供
- 不直接修改导航目标
- VPR 不做物品文本搜索，文本检索来自 SpatialMemory semantic_frame

### D. 单一导航

- 使用 `navigate-to-location`。
- 地点名先通过 Memory 解析为 map 位姿。
- 导航必须等待并验收到达。

### E. 导航后视觉确认

当目标地点存在视觉记忆时，导航完成后执行一次 VPR verify。


流程：

Memory
↓
Navigation
↓
固定姿态采集单张 D455 RGB
↓
VPR search 一次
↓
比较 matched_place_id 与 expected_place_id
↓
地点确认


如果目标地点不存在视觉记忆：

仅执行导航到达验收。


VPR verify 失败后直接返回失败；不得旋转、移动、探索、重复扫描或进行局部搜索。

Navigation 为精确执行 `target_pose` 所做的朝向调整属于正常导航能力。

典型记忆计划：

```text
remember_location(location="工位右侧")
  ↓
remember_visual_location(
  semantic_note="这里有办公桌",
  semantic_tags=["办公桌"]
)
```

典型使用计划：

```text
navigate(semantic_text="办公桌")
  ↓
vpr_verify(place_id_from=navigate step)
```


### F. 多阶段任务

出现以下任一情况即使用 `run-robot-task`：

- 两个及以上动作；
- 条件分支；
- 等待或循环；
- 前一步结果决定下一步；
- 导航与人脸、VPR、TTS、检测、抓取等组合。

需要抓取并评价执行效果时，`grasp` 阶段显式设置
`use_vlac: true` 以使用 `grasp_with_vlac`；未设置时保持原有
`grasp_something` 路径。VLAC 只负责抓取后的视觉 Critic/验证，不代替
Agent 编排、AnyGrasp 检测或机械臂/灵巧手执行。

抓取任务的相机职责必须明确区分：

- `detect_object` 属于通用前向目标检测，使用 D455。
- `grasp` / `grasp_with_vlac` 不依赖 D455 做抓取目标检测。
- 抓取目标检测、深度获取和 AnyGrasp 抓取位姿估计使用斜向下 D435i。
- `grasp_with_vlac` 的 Before / After 抓取执行效果图像也使用 D435i。
- 因此当 `grasp` 返回 `execution_success: false` 时，不得将失败原因推断为“D455 未检测到目标”。
- 若 Robot 端未返回明确错误原因，只能表述为“抓取阶段失败，需要查看 Robot Agent Server / grasp_something 的底层日志”，不得自行猜测具体相机或检测模块失败。

抓取视觉链路：

D435i
→ AnyGrasp / 抓取目标检测
→ grasp_target
→ D435i Before / After
→ VLAC Critic / verify

`run-robot-task` 的职责是把任务翻译为**结构化执行计划**，再交给通用计划执行器。禁止为具体自然语言任务新增 Skill。

## 3. 多阶段执行顺序

1. 解析用户目标和步骤。
2. 生成结构化 plan，明确步骤 ID、类型、输入、条件和超时。
3. 读取本次涉及的基础 Skill 契约；不要读取无关 Skill。
4. 在申请 lease 前完成：地点解析、服务预检、plan 校验、代码编译。
5. 整个物理任务只申请一次 lease，提交一次 `/code/execute`。
6. 机器人端按 plan 顺序执行，每一步输出结构化结果。
7. 条件不满足时标记 `skipped`，不得误执行后续动作。
8. 无论成功、失败或超时，都在 `finally` 释放 lease。
9. 使用 `progress-critic` 根据阶段结果验收，而不是仅凭“命令已提交”。

## 4. SDK 发现策略

SDK Discovery **不是每次任务必做**。

仅在以下情况调用：

- `ROBOT.md` 和 Skill 中没有该能力的方法签名；
- 部署版本变化；
- 服务端明确返回对象或方法不存在；
- 已验证模板与当前接口冲突。

禁止因“想确认一下”而在正常任务中重复读取 `/code/sdk/markdown`。

## 5. 错误处理

### 可在提交前修正一次

- plan 字段缺失；
- JSON 结构错误；
- 地点不存在；
- 必需服务离线。

这些错误必须在 lease 前发现。

### 提交后

- 不让模型现场修改机器人端代码后反复重提。
- 返回失败阶段、错误信息和已完成阶段。
- lease 释放后再向用户汇报。

## 6. 成功标准

- 状态任务：实际接口响应并分类在线/离线。
- 记忆任务：返回 Memory 记录 ID 或可查询记录。
- 导航任务：位置误差和朝向误差达到阈值。
- 感知任务：返回真实识别结果和置信度；未识别到是业务结果，不是代码错误。
- 多阶段任务：每个阶段都有 `success / failed / skipped`，且条件分支正确。

## 7. 时限

- 单次 Agent 运行目标小于 240 秒。
- 若计划的最坏执行时间超过 240 秒，执行前拆分并说明，不通过提高 OpenClaw 总超时掩盖问题。
