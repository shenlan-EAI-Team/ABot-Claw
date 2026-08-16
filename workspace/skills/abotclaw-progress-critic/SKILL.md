---
name: abotclaw-progress-critic
description: Verify stage results and determine whether a G1 task actually succeeded.
---
# Progress Critic

根据结构化结果验收：

- `success`：所有必需阶段成功；
- `partial`：部分阶段完成，但业务条件未满足或可选阶段跳过；
- `failed`：必需阶段失败；
- `skipped`：条件不满足，且没有错误执行。

导航必须检查到达结果/误差；人脸未识别到应标记业务结果，而不是伪造成功；命令提交成功不等于任务成功。

抓取验收必须分开解读：

- `execution_success`：机械臂/灵巧手抓取程序是否执行完成。
- `reward`：VLAC `/critic` 评估 After 相比 Before 的任务进展。
- `done`：VLAC `/grasp/verify` 是否从视觉上确认抓取完成。

不得将 `execution_success=True` 直接解读为 `done=True`。
