# ABot-Claw G1 Workspace v2.0

本版回到原版的基础 Skill 架构：固定 10 个基础 Skill，不为访客接待、人脸后播报等具体任务增加 Skill。

核心改动：

- G1 单机器人，不保留舰队；
- `MISSION.md` 负责单/多任务路由；
- `run-robot-task` 使用结构化 plan 编排多能力；
- 固定机器人端解释器，避免模型临时写完整 Python；
- lease 在 plan 完成后申请；
- 一次 plan 对应一次 lease、一次 execute；
- SDK Discovery 仅在未知接口时触发。
