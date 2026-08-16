---
name: abotclaw-remember-location
description: Create a semantic place memory with current robot pose.
---

# Remember Location


## Purpose

创建地点空间记忆。

负责：

- 获取机器人当前位置
- 创建 place_id
- 保存地点名称和 pose


不负责：

- D455视觉采集
- VPR索引建立


视觉记忆由：

`remember-visual-location`

继续完成。


---

# Trigger


当用户表达：

- 记住当前位置
- 保存这个地点
- 记录这里的位置


调用本 Skill。


---

# Workflow


用户：

"记住当前位置为训练场南侧"


执行：



获取机器人当前pose

↓

SpatialMemory创建place

↓

生成place_id

↓

保存:

place_name

pose

↓

返回place_id



---

# Output


返回：


place_id

place_name

pose



---

# Notes


- 不移动机器人。
- 不采集图片。
- 不调用VPR。
- 创建的place_id会传递给remember-visual-location。
