---
name: abotclaw-bundle
description: Package the G1 workspace or a base skill for deployment and audit.
---
# Bundle

只打包实际依赖，排除 `.git`、缓存和会话 memory。打包后校验 Skill 数量必须为 10，且不得包含任务专用 Skill。
