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
- `detect_object`：D455 前向检测
- `grasp`：D435i 抓取
- `release`：释放物体
- `wait`：短时等待

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
