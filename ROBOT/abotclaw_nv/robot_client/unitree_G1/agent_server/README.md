# G1 Agent Server

基于 FastAPI 的硬件侧 Agent：租约（lease）、代码执行（`/code/execute`）、状态与相机等。默认监听 **8888** 端口。

## 启动

```bash
cd /home/szm/packages/ABot-Claw-main/robot_client/unitree_G1/agent_server
python3 server.py --host 0.0.0.0 --port 8888
```

- 本机访问：`http://127.0.0.1:8888`；局域网访问需 `--host 0.0.0.0`。
- 可选：`--no-service-manager`（不启后台 ServiceManager，减少启动时 `pkill`/清理对环境的干扰）。
- 可选：`--no-dashboard`（关闭服务管理仪表盘相关路由）。

### 后台长期运行

仅用 `&` 可能在终端关闭或收到 SIGHUP 时退出，建议：

```bash
nohup python3 server.py --host 0.0.0.0 --port 8888 > /tmp/g1_agent_server.log 2>&1 &
```

或用 `systemd` / `tmux` 管理进程，便于自动重启与日志轮转。

## API：Lease 与代码执行

`/code/execute` **必须**携带有效租约，否则返回 **401**（`Missing X-Lease-Id header`）。

### 1. 申请租约

```bash
curl -s -X POST http://localhost:8888/lease/acquire \
  -H "Content-Type: application/json" \
  -d '{"holder": "my_client"}'
```

响应中的 `lease_id` 用于后续请求头。

### 2. 执行代码（异步）

```bash
curl -s -X POST http://localhost:8888/code/execute \
  -H "Content-Type: application/json" \
  -H "X-Lease-Id: <上一步的 lease_id>" \
  -d '{"code": "print(\"hello\")"}'
```

返回中含 **`execution_id`**（短 ID）。执行在后台进行，接口立即返回。

### 3. 查询执行状态与输出

轮询 **`GET /code/status`**（只读，无需 lease）：

```bash
curl -s http://localhost:8888/code/status
```

响应中包含是否正在运行、当前 `execution_id`、stdout/stderr 片段等。  
文档与实现均以 **`/code/status`** 为准；不要用不存在的路径猜测「执行详情」接口。

### 4. 停止与释放

- 停止当前代码：`POST /code/stop`（需 header `X-Lease-Id`）。
- 释放租约：`POST /lease/release`，body：`{"lease_id": "<lease_id>"}`。

用完机器人后应 **release**，避免 lease 长期被占用。

## 其他常用端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 根路径；开 dashboard 时可能重定向 |
| GET | `/docs` | OpenAPI 文档 |
| GET | `/health` | 健康检查与 lease 摘要 |
| GET | `/state` | 当前聚合状态快照 |
| GET | `/code/recordings/{execution_id}` | 某次执行的录制元数据与路径说明 |

更完整的 HTTP API 以 **`/docs`** 与 `routes/` 下实现为准。

## 日志与调试

- 主进程日志：使用项目内 `logging_config.setup_logging("agent_server")`；子模块如 **`utils.ik`** 在 setup 中会单独挂到 INFO，便于 IK/RobotBridge 诊断。
- 抓取与 IK：见 `robot_sdk/README.md`、`robot_sdk/g1_grasp_sdk.py` 模块说明。

## 目录结构（节选）

```
agent_server/
├── server.py              # 入口
├── logging_config.py
├── state.py               # 状态聚合（可选 RobotBridge，默认不建全局 DDS）
├── routes/                # FastAPI 路由
├── robot_sdk/             # YOLO、D435i、抓取等（见 robot_sdk/README.md）
└── utils/ik/              # G1 IK、G1IKController
```

## 常见问题

| 现象 | 处理 |
|------|------|
| `Connection refused` | 确认本机已启动 `server.py` 且端口未被占用 |
| `Missing X-Lease-Id header` | 先 `POST /lease/acquire`，再在 `/code/execute` 带 `X-Lease-Id` |
| 后台启动后很快退出 | 使用 `nohup` 或 systemd；必要时加 `--no-service-manager` 排查 |
| 手臂状态始终为空 | 默认不在 Server 进程内创建全局 RobotBridge；手臂状态仅在接入 bridge 或子进程抓取时才有 |
| **抓取流程成功但真机臂不动** | ① `/code/execute` 子进程现与 **Agent 同一 `sys.executable`**，请在该环境中安装 `unitree_sdk2py`。② 设置 **`G1_ARM_NETWORK_IFACE`**（或 `UNITREE_IFACE` / `G1_NETWORK_INTERFACE`）为连机器人的网卡。③ 看子进程 stdout 是否出现 **`未收到 rt/lowstate`**：无 LowState 时 DDS 未通到机器人，指令可能无效。 |

### 环境变量（抓取 / DDS）

| 变量 | 含义 |
|------|------|
| `G1_ARM_NETWORK_IFACE` / `UNITREE_IFACE` / `G1_NETWORK_INTERFACE` | `ChannelFactoryInitialize` 使用的网卡名（默认 `enp4s0`） |
