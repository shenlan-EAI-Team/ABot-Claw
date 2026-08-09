# G1 Execution Plan Schema

```json
{
  "steps": [
    {"id":"go_entry","type":"navigate","location":"工位","timeout_s":75},
    {"id":"face","type":"face_wait","target":"赵春波","timeout_s":30},
    {"id":"welcome","type":"speak","text":"你好，欢迎来到深蓝学院，请跟我来","when":{"step":"face","field":"matched","equals":true}},
    {"id":"go_bar","type":"navigate","location":"吧台","when":{"step":"face","field":"matched","equals":true}},
    {"id":"arrival","type":"speak","text":"这里是教室，请先坐下休息","when":{"step":"go_bar","field":"status","equals":"success"}}
  ]
}
```

字段：

- `id`：唯一步骤名。
- `type`：`navigate | face_wait | speak | detect_object | grasp | release | wait`。
- `when`：可选条件，引用已完成步骤。
- `continue_on_failure`：默认 false。
- `timeout_s`：步骤超时，不得使整个 plan 超过配置上限。
