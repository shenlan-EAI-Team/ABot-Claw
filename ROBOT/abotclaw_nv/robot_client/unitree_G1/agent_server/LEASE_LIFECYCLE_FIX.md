# Lease 生命周期修复说明（v1）

本次修改把一次已接受的 `/code/execute` 与拥有它的 Lease 明确绑定。不修改
Workspace、SDK 业务逻辑、硬件初始化方式，也不调整任何 timeout 数值。

## 状态约束

| Lease 状态 | 含义 | 是否检查 idle timeout | 能否发下一张 Lease |
| --- | --- | --- | --- |
| `held` | Lease 已取得，尚未绑定 execution | 是 | 否 |
| `running` | 已绑定 `execution_id` | 否 | 否 |
| `ending` | 已请求停止/释放，execution 正在收尾 | 否 | 否 |
| free | 当前无 Lease | 不适用 | 是 |

状态转换：

1. `held -> running`：`/code/execute` 先预留 CodeExecutor 执行槽，再由
   `LeaseManager.bind_execution()` 绑定同一个 `execution_id`。
2. `running -> free`：任务正常返回后，由
   `LeaseManager.finish_execution()` 执行真正释放。
3. `running -> ending`：`/lease/release`、`/code/stop`、clear queue 或 Lease
   hard max 都只请求停止当前 Lease 精确绑定的 execution。
4. `ending -> free`：必须等该 execution 返回，Route 收尾执行到
   `finish_execution()` 后才真正释放。
5. 只有当前 Lease 真正 free（以及可选 reset-on-release 完成）之后，队列下一位
   才允许获得 Lease。

## 兼容性约束

- `/code/status.is_running` 继续作为现有调用方的“任务是否仍活动”标志；
  `starting` 与 `running` 阶段都返回 `true`。
- 新增 `starting` 执行状态，覆盖 `/code/execute` 已接受但 `Popen` 尚未创建的空窗。
- 已绑定 `/code/execute` 的任务不再因为 Lease idle 检测被中途撤销；CodeExecutor
  timeout 与 Lease hard max 仍然生效。
- execution 活动期间主动 release 会请求结束任务，但 Lease 保持 `ending`，直到
  execution 完成收尾。

## 本次故意不修改的时间参数

- Lease idle timeout：60 s
- Lease hard max：180 s
- 默认 code execution timeout：180 s

建议先在实机验证生命周期，再单独调整 timeout budget。当前两个 180 s 硬截止时间
相同，仍可能出现“最终由哪一层先报告 timeout”的竞争，但修复后不会再因此让新 Lease
与旧 execution 重叠。

## 验证

在 Agent Server 目录执行：

```bash
python tests/test_lease_lifecycle.py
python -m compileall -q .
```

回归测试覆盖：绑定任务结束前不提前释放、运行任务跳过 idle 但服从 hard max，以及
已接受但尚未 `Popen` 的 `starting` 窗口能够被正确取消。

## v1 明确未解决的内容

本次保证旧的 `CodeExecutor.execute()` 尚未完成收尾时，不会把 Lease 所有权交给下一
任务；但尚未保证子进程被强制 kill 时每个 SDK 的 Python `shutdown()` 都一定执行。
signal-safe bootstrap / SDK 资源清理应在本次 Lease 修复完成实机验证后单独处理。
