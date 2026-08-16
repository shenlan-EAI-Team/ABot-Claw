---
name: abotclaw-run-robot-task
description: Compose multiple verified G1 primitives into one conditional sequential execution plan.
---
# Run Robot Task

用于多步骤、条件、等待或循环任务。它是**通用编排器**，不是具体任务 Skill。

## 支持的计划步骤

- `navigate`：导航到某地点
- `face_wait`：等待指定人或任一已知人脸
- `speak`：TTS
- `detect_object`：使用 D455 的通用前向目标检测；它不是抓取前置步骤，除非用户任务明确要求单独进行前向目标检测。
- `grasp`：抓取内部使用斜向下 D435i 获取目标 RGB/Depth，并通过 AnyGrasp 生成抓取位姿；默认调用 `grasp_something`。
- `grasp` 显式设置 `use_vlac: true` 时调用 `grasp_with_vlac`；抓取执行和 VLAC Before / After 评估均使用 D435i，返回 `execution_success + reward + done`。
- `release`：释放物体
- `wait`：短时等待

## 抓取任务约束

对于 `grasp`：

1. 不要为了抓取任务自动插入 `detect_object` 步骤。
2. 不要使用 D455 的检测结果判断 D435i 抓取目标是否存在。
3. `grasp_something` / `grasp_with_vlac` 会在 Robot 端自行使用 D435i + AnyGrasp 完成抓取目标检测和位姿生成。
4. 当 `execution_success=false` 且没有明确错误字段时，不得猜测为“D455 未检测到瓶子”或“AnyGrasp 未生成姿态”。
5. 此时应返回“抓取阶段失败，需查看 Robot Agent Server 底层抓取日志”。
## 执行方法

1. 将用户任务转换为 JSON plan。
2. 运行：

```bash
python3 skills/abotclaw-run-robot-task/scripts/run_plan.py --plan-file /tmp/g1-plan.json
```

3. plan 只描述步骤和条件，不包含 Python 代码。
4. 执行器在 lease 前解析地点、检查服务、校验时限并编译固定机器人程序。
5. 整个 plan 一次 lease、一次 `/code/execute`、一次 release。

详细 schema：`references/plan-schema.md`。
