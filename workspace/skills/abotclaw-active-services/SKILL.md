---
name: abotclaw-active-services
description: Check G1 PC-side Memory, YOLO, Face and AnyGrasp services.
---
# Active Services

执行：

```bash
python3 skills/abotclaw-active-services/scripts/check_services.py
```

并行检查服务。Spatial Memory、YOLO、Face 和 AnyGrasp 均作为当前部署的必需服务。任一必需服务离线时，状态检查应明确报告失败。
