# Architecture

```text
User
  ↓
AGENTS / MISSION 路由
  ├─ 单能力 → 对应基础 Skill
  └─ 多阶段 → run-robot-task
                  ↓
              JSON plan
                  ↓
        通用 plan 校验与地点解析
                  ↓
         一次 lease + 一次 execute
                  ↓
 navigate / face / TTS / detect / grasp 原语
                  ↓
          结构化阶段结果
                  ↓
          progress-critic 验收
```

Skill 数量固定为 10。任务扩展通过增加通用原语或改进现有 Skill 内部实现完成，不以自然语言任务名称增加 Skill。
