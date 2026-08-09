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
