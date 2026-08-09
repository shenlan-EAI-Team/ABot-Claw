---
name: abotclaw-memory
description: Generic Spatial Memory query and upsert operations for G1 places and objects.
---
# Spatial Memory

负责地点/对象记忆的通用查询和写入，不负责导航执行。

地点查询：

```bash
python3 skills/abotclaw-memory/scripts/memory_cli.py query-place --name "工位"
```

地点写入由 `remember-location` 调用同一 Memory 契约。地点结果必须保留完整 map 位姿。
