# TOOLS.md — Deployment and Tool Boundaries

## 配置

部署地址、端口、网卡和超时统一读取：

`config/deployment.yaml`

禁止在多个 Markdown 文件中复制当前 IP。

## 本机与机器人端

- 本机 Exec/Python：健康检查、Memory HTTP、lease、提交代码、轮询结果。
- 机器人端 `/code/execute`：只运行 G1 SDK 业务逻辑。
- 私网地址禁止使用 Web Fetch；使用 Exec 内的 curl 或 Python `urllib`。

## 执行规则

- 优先执行 Skill 自带的确定性脚本。
- 已知流程不得临时写 heredoc 机器人代码。
- 多阶段任务只生成小型 JSON plan，机器人程序由 `run-robot-task` 的固定执行器生成。
- 不在正常任务中遍历整个 workspace、`/services` 或所有文档。

## 日志

OpenClaw Gateway：

```bash
journalctl --user -u openclaw-gateway -f -o short-precise
```

当日日志：

```bash
/tmp/openclaw/openclaw-YYYY-MM-DD.log
```
