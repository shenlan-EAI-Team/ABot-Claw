---
name: abotclaw-robot-connection
description: Check G1 Agent Server and live body state without changing robot state.
---
# Robot Connection

用于“检查机器人连接/本体状态”。

执行：

```bash
python3 skills/abotclaw-robot-connection/scripts/check_robot.py
```

输出必须来自实时 `/health` 和 `/state`。本 Skill 不检查全部 PC 服务，不申请 lease。
