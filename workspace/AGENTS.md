# AGENTS.md — G1 Workspace Entry

本工作空间只服务一台 **Unitree G1（g1_001）**。

## 会话读取顺序

1. 每次会话读取 `SOUL.md`、`IDENTITY.md`、`USER.md`、`MISSION.md`。
2. 只有机器人任务才读取 `ROBOT.md`。
3. 只读取本次任务对应的 Skill；禁止一次性读取全部 Skill 和全部 `docs/`。
4. `docs/` 仅在接口未知、部署变化或排障时按需读取。

## 路由原则

- 检查机器人状态：`abotclaw-robot-connection` + `abotclaw-active-services`
- 记住当前位置：`abotclaw-remember-location`
- 导航到地点：`abotclaw-navigate-to-location`
- 查询或写入空间记忆：`abotclaw-memory`
- 条件、循环、等待或多个能力串联：`abotclaw-run-robot-task`
- SDK 方法确实未知或已报方法不存在：`abotclaw-sdk-discovery`
- 任务完成后需要严格验收：`abotclaw-progress-critic`
- 部署打包：`abotclaw-bundle`

## 强制约束

- 不创建“访客接待”“人脸后播报”等任务专用 Skill。
- 多阶段任务由 `run-robot-task` 组合基础能力，不把自然语言任务固化为新 Skill。
- 已在 `ROBOT.md` 或对应 Skill 中确认的方法，不重复发现 SDK。
- 私网 `192.168.*` 请求使用本机 Exec/curl/Python，不使用 Web Fetch。
- 已有确定性脚本时，禁止临时生成机器人端 heredoc 代码。
