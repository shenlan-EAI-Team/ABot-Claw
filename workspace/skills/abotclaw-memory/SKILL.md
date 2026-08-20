---

name: abotclaw-memory
description: Generic Spatial Memory query and upsert operations for G1 places and objects.
------------------------------------------------------------------------------------------

# Spatial Memory

负责地点/对象记忆的通用查询和写入，不负责导航执行。

## 查询指定地点

当用户询问某个具体地点的位置、记忆或相关信息时，使用：

```bash
python3 skills/abotclaw-memory/scripts/memory_cli.py query-place --name "工位"
```

可通过 `--n-results` 控制返回数量。

例如：

```bash
python3 skills/abotclaw-memory/scripts/memory_cli.py query-place \
  --name "李明宇工位" \
  --n-results 5
```

地点查询结果必须保留完整的 map 位姿信息。

## 列出所有记忆地点

当用户表达以下意图时：

* 列出所有记忆地点
* 当前有哪些地点
* 已经记住了哪些位置
* 展示地点列表
* 有哪些地点可以导航
* 机器人记住了哪些地点

必须直接调用：

```bash
python3 skills/abotclaw-memory/scripts/memory_cli.py list-places
```

`list-places` 内部固定调用 Spatial Memory：

```text
POST /query/place
```

请求体：

```json
{
  "name": "",
  "n_results": 100
}
```

空字符串 `name=""` 表示列出当前可查询到的地点。

列出所有地点时，不要：

* 查询 OpenAPI 寻找其他接口
* 尝试 `/query/unified`
* 枚举 `place_id`
* 猜测其他 list API
* 因缺少地点名称而放弃查询

直接使用 `list-places` 返回结果中的 `results`。

向用户展示地点时，优先保留：

* `name`
* `place_id`
* `evidence.note`（若存在）
* 必要时保留 `target_pose`

如果存在同名地点但 `place_id` 不同，不要自行合并，除非用户明确要求去重或整理。

## 地点写入

地点写入由 `remember-location` 调用同一 Spatial Memory 契约。

通用写入也可通过：

```bash
python3 skills/abotclaw-memory/scripts/memory_cli.py upsert-place \
  --json-file /path/to/place.json
```

地点结果必须保留完整 map 位姿。

