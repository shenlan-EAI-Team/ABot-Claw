"""Hardware server — FastAPI app wiring everything together."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import signal
import subprocess
import sys
import time

# Add project root to path
_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SERVER_DIR)
for _p in [_PROJECT_ROOT, _SERVER_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 强制：ROS1 Python 包优先级最高，防止 conda 环境中 ROS2 Humble 干扰
_ROS1_PATH = "/opt/ros/noetic/lib/python3/dist-packages"
if _ROS1_PATH not in sys.path:
    sys.path.insert(0, _ROS1_PATH)
else:
    # 把它挪到最前面
    sys.path.remove(_ROS1_PATH)
    sys.path.insert(0, _ROS1_PATH)

# 从 sys.path 中移除 ROS2 Humble 的干扰路径（如果有的话）
_HUMBLE_PYTHONPATH = "/opt/ros/humble/local/lib/python3.10/dist-packages"
while _HUMBLE_PYTHONPATH in sys.path:
    sys.path.remove(_HUMBLE_PYTHONPATH)

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from auth import APIKeyMiddleware, KeyStore
from config import LeaseConfig, ServerConfig, ServiceManagerConfig, default_services
from lease import LeaseManager
from display_state import DisplayBroadcaster
from services import ServiceManager
from state import StateAggregator

from logging_config import setup_logging

logger = setup_logging("agent_server")


def _make_robotbridge() -> "RobotBridge | None":
    """在需要时延迟创建 RobotBridge 实例（独立于 g1_velocity_control_sdk 节点）。"""
    import os

    robot_ip = os.environ.get("G1_ROBOT_IP", "192.168.123.164")
    iface = os.environ.get(
        "G1_ARM_NETWORK_IFACE",
        os.environ.get("UNITREE_IFACE", os.environ.get("G1_NETWORK_INTERFACE", "enp4s0")),
    )
    domain = int(os.environ.get("G1_ROBOT_DOMAIN", "0"))
    try:
        # 动态导入，避免启动阶段 cyclonedds 报错时直接炸掉整个 server
        from robot_sdk.robotbridge import RobotBridge

        logger.info(
            "Creating RobotBridge for state monitoring (robot=%s, iface=%s, domain=%d)",
            robot_ip, iface, domain,
        )
        rb = RobotBridge(
            iface=iface,
            domain=domain,
            default_mode=0,
        )
        if rb.ok:
            logger.info("RobotBridge connected successfully")
        else:
            logger.warning("RobotBridge created but not yet OK — robot may be offline or not StandUp")
        return rb
    except Exception as e:
        logger.error("Failed to create RobotBridge: %s", e)
        return None


def _find_listen_pids(port: int) -> list[int]:
    """返回在 ``port`` 上 TCP LISTEN 的进程 PID（不含当前进程）。"""
    pids: set[int] = set()
    mypid = os.getpid()
    try:
        r = subprocess.run(
            ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    pid = int(line)
                    if pid != mypid:
                        pids.add(pid)
                except ValueError:
                    continue
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if pids:
        return sorted(pids)
    try:
        r = subprocess.run(
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.stdout:
            for m in re.finditer(r"pid=(\d+)", r.stdout):
                pid = int(m.group(1))
                if pid != mypid:
                    pids.add(pid)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return sorted(pids)


def _kill_listeners_on_port(port: int) -> None:
    """若本机 ``port`` 已被其它进程监听，则 SIGTERM 后必要时 SIGKILL，便于本服务重新绑定。"""
    pids = _find_listen_pids(port)
    if not pids:
        return
    for pid in pids:
        try:
            logger.warning("端口 %d 已被 PID %d 占用，发送 SIGTERM …", port, pid)
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as e:
            logger.error("无法结束占用端口的进程 PID %d（权限不足）: %s", pid, e)

    for _ in range(30):
        time.sleep(0.1)
        if not _find_listen_pids(port):
            logger.info("端口 %d 已释放", port)
            return

    for pid in _find_listen_pids(port):
        try:
            logger.warning("PID %d 仍监听端口 %d，发送 SIGKILL", pid, port)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as e:
            logger.error("SIGKILL PID %d 失败: %s", pid, e)


def build_app(cfg: ServerConfig, service_mgr: ServiceManager | None = None) -> FastAPI:
    app = FastAPI(title="Unitree G1 Robot Agent Server")

    # -- API key auth --------------------------------------------------------
    keys_path = os.path.join(_SERVER_DIR, "api_keys.json")
    key_store = KeyStore(keys_path)
    app.add_middleware(APIKeyMiddleware, key_store=key_store)
    app.state.key_store = key_store

    app.state.background_tasks = set()

    @app.get("/", include_in_schema=False)
    async def root():
        if cfg.dashboard:
            return RedirectResponse(url="/services/dashboard")
        return {"status": "ok", "message": "Unitree G1 Robot Agent Server", "docs": "/docs"}

    # -- 状态聚合器 ---------------------------------------------------------
    # 不在此进程内全局创建 RobotBridge：DDS ChannelFactory + LowState 订阅可能因网卡/SDK
    # 在原生层崩溃，或与抓取任务里 G1IKController 再次创建 RobotBridge 冲突。需要手臂状态
    # 时再考虑单独进程或显式 env 开关；抓取仍通过 g1_grasp_sdk → G1IKController 自带实例。
    # RobotBridge 实例用于 /state 轮询（独立于 g1_velocity_control_sdk 节点）
    rb = _make_robotbridge()
    state_agg = StateAggregator(poll_hz=cfg.base.poll_hz, robotbridge=rb)

    display = DisplayBroadcaster()

    lease_mgr = LeaseManager(
        cfg.lease,
        last_moved_at_fn=state_agg.last_moved_at,
    )

    # -- routes --------------------------------------------------------------
    from routes.lease_routes import create_router as lease_router
    from routes.state_routes import create_router as state_router
    from routes.ws import create_router as ws_router
    from routes.code_routes import init_code_routes, get_executor
    from routes.sdk_docs import router as sdk_docs_router
    from routes.system_guide import router as system_guide_router
    from routes.yolo_routes import router as yolo_router
    from routes.display_routes import create_router as display_router
    from routes.camera_routes import router as camera_router, cleanup_camera
    from routes.grasp_routes import router as grasp_router
    from routes.navigation_routes import router as nav_router
    from routes.face_routes import router as face_router
    from routes.memory_routes import router as memory_router
    from routes.tts_routes import router as tts_router

    # Couple lease ownership to the exact /code/execute lifecycle without
    # introducing imports from LeaseManager back into the route layer.
    lease_mgr.set_execution_callbacks(
        is_active=lambda execution_id: get_executor().is_execution_active(execution_id),
        stop=lambda execution_id, reason: get_executor().stop_execution(execution_id, reason),
    )

    # IK controller is NOT exposed to submitted code by design.

    # Register camera routes FIRST to override state_routes' /cameras endpoint
    app.include_router(camera_router)
    app.include_router(state_router(state_agg, None, lease_mgr, None, None, None, None, None))
    app.include_router(lease_router(lease_mgr))
    app.include_router(ws_router(state_agg, cfg, None, key_store=key_store))
    app.include_router(init_code_routes(lease_mgr, None, state_agg))
    app.include_router(sdk_docs_router)
    app.include_router(system_guide_router)
    # GET /yolo/visualization — latest annotated JPEG (save_detection_image → ~/d435i_yolo_*.jpg, or /tmp/yolo_viz/latest.jpg)
    app.include_router(yolo_router)
    app.include_router(display_router(display, key_store=key_store))
    app.include_router(grasp_router)
    app.include_router(nav_router)
    app.include_router(face_router)
    app.include_router(memory_router)
    app.include_router(tts_router)

    # Service manager routes (includes dashboard)
    if cfg.dashboard and service_mgr is not None:
        from routes.service_routes import create_router as service_router
        app.include_router(service_router(service_mgr))

    # -- lifecycle -----------------------------------------------------------
    @app.on_event("startup")
    async def startup():
        logger.info("Starting Unitree G1 Agent Server")

        if service_mgr is not None:
            await service_mgr.start()

        await state_agg.start()
        await lease_mgr.start()

        # Display status polling (1 Hz)
        async def _display_status_loop():
            prev_running = False
            while True:
                try:
                    executor = get_executor()
                    is_running = executor.is_busy
                    lease_status = lease_mgr.status()
                    queue_length = lease_status.get("queue_length", 0)
                    holder = lease_status.get("holder", "") or ""

                    status = "executing" if is_running else "idle"
                    display.update_robot_status(status, queue_length, holder)

                    if prev_running and not is_running:
                        display.on_execution_ended()
                    prev_running = is_running
                except Exception:
                    pass
                await asyncio.sleep(1.0)

        def _display_done(t: asyncio.Task) -> None:
            app.state.background_tasks.discard(t)
            try:
                t.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Display status loop task crashed")

        task = asyncio.create_task(_display_status_loop())
        app.state.background_tasks.add(task)
        task.add_done_callback(_display_done)

        if key_store.enabled:
            logger.info("API key auth ENABLED (%d keys loaded)", len(key_store._keys))
        else:
            logger.info("API key auth DISABLED (no keys configured)")
        logger.info("Unitree G1 Agent Server ready on %s:%d", cfg.host, cfg.port)

    @app.on_event("shutdown")
    async def shutdown():
        logger.info("Shutting down Unitree G1 Agent Server")

        try:
            executor = get_executor()
            if executor.is_busy:
                executor.stop()
            executor.cleanup_old_code_files()
        except Exception as e:
            logger.warning("Failed to cleanup code executor: %s", e)

        await lease_mgr.stop()
        await state_agg.stop()

        if service_mgr is not None:
            await service_mgr.stop()
        
        # Cleanup camera resources
        cleanup_camera()

    return app


def main():
    parser = argparse.ArgumentParser(description="Unitree G1 Robot Agent Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8888)

    parser.add_argument(
        "--auto-start-services",
        action="store_true",
        help="Auto-start backend services on startup",
    )
    parser.add_argument(
        "--no-service-manager",
        action="store_true",
        help="Disable service management entirely",
    )
    parser.add_argument(
        "--no-reset-on-release",
        action="store_true",
        help="Disable auto-reset when lease ends",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Disable the web dashboard",
    )
    args = parser.parse_args()

    svc_mgr_cfg = ServiceManagerConfig(
        enabled=not args.no_service_manager,
        auto_start=args.auto_start_services,
    )
    lease_cfg = LeaseConfig()
    if args.no_reset_on_release:
        lease_cfg.reset_on_release = False

    cfg = ServerConfig(
        host=args.host,
        port=args.port,
        service_manager=svc_mgr_cfg,
        lease=lease_cfg,
        dashboard=not args.no_dashboard,
    )

    service_mgr = None
    if cfg.service_manager.enabled:
        service_mgr = ServiceManager(
            config=cfg.service_manager,
            services=default_services(),
            dry_run=False,
        )

    app = build_app(cfg, service_mgr=service_mgr)
    _kill_listeners_on_port(cfg.port)
    # workers=1：单进程；避免多 worker 下重复绑定 DDS/资源。timeout_graceful_shutdown 便于收到 SIGTERM 时收尾。
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        access_log=False,
        workers=1,
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
