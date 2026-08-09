---
name: abotclaw-remember-location
description: Save the G1 current map pose under a semantic place name.
---
# Remember Location

执行：

```bash
python3 skills/abotclaw-remember-location/scripts/remember_location.py --name "训练场中央"
```

流程：读取实时位姿 → 写入 Spatial Memory → 返回记录。该任务不运动机器人，不申请 lease。
