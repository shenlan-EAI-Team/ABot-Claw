---
name: abotclaw-sdk-discovery
description: Discover G1 runtime SDK only when a method is unknown or deployment changed.
---
# SDK Discovery

不是每次机器人任务的前置步骤。

仅在方法未知、部署升级或服务端明确报不存在时执行：

1. `GET /code/sdk/modules`
2. `GET /code/sdk/markdown`
3. 必要时 `GET /openapi.json`

发现结果只补充到对应 Skill 或 `ROBOT.md` 的稳定事实中，避免下一次重复发现。私网请求使用 Exec，不使用 Web Fetch。
