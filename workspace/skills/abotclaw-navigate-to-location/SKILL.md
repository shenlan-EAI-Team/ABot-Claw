---
name: abotclaw-navigate-to-location
description: Resolve a named place from Spatial Memory, navigate G1, and verify arrival.
---
# Navigate to Location

执行：

```bash
python3 skills/abotclaw-navigate-to-location/scripts/navigate_to_location.py --name "工位"
```

流程：Memory 解析完整位姿 → 申请 lease → 一次 execute → 等待到达 → 误差验收 → release。

多阶段任务不要逐次运行此 CLI；由 `run-robot-task` 复用同一导航原语，在一个 lease/execute 内完成。
