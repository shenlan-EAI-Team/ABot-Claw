#!/usr/bin/env python3
"""
SpatialMemory 交互式测试脚本

用法:
  python test_memory_interactive.py              # 交互模式
  python test_memory_interactive.py --data-dir /path/to/data  # 指定数据目录
  python test_memory_interactive.py --cmd <command> [args...]  # 命令行模式
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from PIL import Image

from app.services.memory_service import MemoryService
from app.storage import SqliteStore
from app.schemas import (
    Pose,
    ObjectMemoryUpsertRequest,
    PlaceMemoryUpsertRequest,
    SemanticFrameIngestRequest,
    UnifiedQuery,
)


# ---------------------------------------------------------------------------
# 数据目录
# ---------------------------------------------------------------------------

def resolve_data_dir(user_dir: str | None) -> Path:
    if user_dir:
        d = Path(user_dir).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    base = Path(__file__).resolve().parent
    default = base / "data"
    default.mkdir(parents=True, exist_ok=True)
    return default


# ---------------------------------------------------------------------------
# Service 实例
# ---------------------------------------------------------------------------

def make_service(data_dir: Path) -> MemoryService:
    import os
    os.environ["MEMORY_HUB_DATA_DIR"] = str(data_dir)
    import importlib
    import app.config as cfg_module
    importlib.reload(cfg_module)
    import app.services.memory_service as ms_module
    importlib.reload(ms_module)
    store = SqliteStore(data_dir / "memory_hub.db")
    return ms_module.MemoryService(store)


# ---------------------------------------------------------------------------
# 辅助：生成小测试图
# ---------------------------------------------------------------------------

def make_test_image(color: tuple[int, int, int] = (120, 180, 240), size: int = 64) -> str:
    img = Image.new("RGB", (size, size), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ---------------------------------------------------------------------------
# 交互模式
# ---------------------------------------------------------------------------

HELP_TEXT = """
可用命令:
  health                          - 查看服务健康状态
  list [object|place|semantic|keyframe]  - 列出指定类型的所有记忆
  list-all                       - 列出所有记忆
  search <关键词>                 - 搜索名称包含关键词的记忆
  find <名称>                     - 精确查找对象或地点
  near <x> <y> [radius=2.0]      - 查询指定坐标附近的记忆
  add-object <名称> [x=0 y=0]    - 添加对象记忆
  add-place <名称> [x=0 y=0]      - 添加地点记忆
  add-semantic <描述>             - 添加语义帧记忆
  delete <id>                    - 删除指定记忆
  demo                           - 写入演示数据（茶水间等）
  help                           - 显示此帮助
  quit / exit                    - 退出
""".strip()


def fmt_result(r) -> str:
    pose = r.target_pose
    ev = r.evidence or {}
    lines = [
        f"  [{r.memory_type}] {r.name}  (id={r.id})",
        f"    机器人: {r.robot_id} / {r.robot_type}",
        f"    位置: x={pose.x:.2f} y={pose.y:.2f} z={pose.z:.2f}",
        f"    置信度: {r.confidence:.2f}  时间: {time.ctime(r.timestamp)}",
        f"    备注: {r.source} | {ev.get('note', '')}",
    ]
    return "\n".join(lines)


def cmd_health(svc: MemoryService) -> str:
    h = svc.health()
    return (
        f"状态: {h['status']}\n"
        f"版本: {h['version']}\n"
        f"当前记忆总数: {h['records']}\n"
        f"数据目录: {h['data_dir']}"
    )


def cmd_list(svc: MemoryService, memory_type: str) -> str:
    rows = svc.store.all_memories(memory_type=memory_type if memory_type != "all" else None)
    if not rows:
        return f"没有找到 {memory_type} 类型的记忆"
    lines = [f"共 {len(rows)} 条记忆:"]
    for row in rows:
        pose_str = f"({row['x']:.2f}, {row['y']:.2f}, {row['z']:.2f})"
        lines.append(f"  [{row['memory_type']}] {row['name']} | id={row['id']} | 位置={pose_str} | robot={row['robot_id']}")
    return "\n".join(lines)


def cmd_search(svc: MemoryService, keyword: str) -> str:
    rows = svc.store.query_memories(name=keyword, limit=50)
    if not rows:
        return f"没有找到名称包含「{keyword}」的记忆"
    results = [svc._row_to_result(row) for row in rows]
    lines = [f"找到 {len(results)} 条匹配的记忆:"]
    for r in results:
        lines.append(fmt_result(r))
    return "\n".join(lines)


def cmd_find(svc: MemoryService, name: str) -> str:
    results = svc.query_by_name("object", name, n_results=5, robot_id=None)
    results += svc.query_by_name("place", name, n_results=5, robot_id=None)
    results += svc.query_by_name("semantic_frame", name, n_results=5, robot_id=None)
    if not results:
        return f"没有找到「{name}」"
    lines = [f"找到 {len(results)} 条关于「{name}」的记忆:"]
    for r in results:
        lines.append(fmt_result(r))
        pose = r.target_pose
        lines.append(f"    >>> 地址: x={pose.x:.4f}, y={pose.y:.4f}, z={pose.z:.4f}, frame={pose.frame_id}")
    return "\n".join(lines)


def cmd_near(svc: MemoryService, x: float, y: float, radius: float) -> str:
    results = svc.query_by_position(x, y, radius, n_results=20)
    if not results:
        return f"坐标 ({x}, {y}) 半径 {radius}m 范围内没有记忆"
    lines = [f"坐标 ({x}, {y}) 半径 {radius}m 范围内共 {len(results)} 条记忆:"]
    for r in results:
        pose = r.target_pose
        dist = ((pose.x - x) ** 2 + (pose.y - y) ** 2) ** 0.5
        lines.append(fmt_result(r))
        lines.append(f"    >>> 距离: {dist:.2f}m | 地址: x={pose.x:.4f}, y={pose.y:.4f}, z={pose.z:.4f}")
    return "\n".join(lines)


def cmd_add_object(svc: MemoryService, name: str, x: float, y: float) -> str:
    req = ObjectMemoryUpsertRequest(
        object_name=name,
        robot_id="test_robot",
        robot_type="test",
        robot_pose=Pose(x=0, y=0, z=0),
        object_pose=Pose(x=x, y=y, z=0),
        detect_confidence=0.9,
        image=make_test_image(color=(100, 200, 100)),
        note="test",
    )
    result = svc.upsert_object(req)
    return f"对象写入成功: id={result['id']}"


def cmd_add_place(svc: MemoryService, name: str, x: float, y: float) -> str:
    req = PlaceMemoryUpsertRequest(
        place_name=name,
        robot_id="test_robot",
        robot_type="test",
        place_pose=Pose(x=x, y=y, z=0, yaw=1.57),
        alias=[],
        note="",
    )
    result = svc.upsert_place(req)
    return f"地点写入成功: id={result['id']}"


def cmd_add_semantic(svc: MemoryService, note: str) -> str:
    req = SemanticFrameIngestRequest(
        robot_id="test_robot",
        robot_type="test",
        robot_pose=Pose(x=0, y=0, z=0),
        image=make_test_image(color=(200, 150, 100)),
        note=note,
        tags=[],
    )
    result = svc.ingest_semantic_frame(req)
    return f"语义帧写入成功: id={result['id']}"


def cmd_demo(svc: MemoryService) -> str:
    demo_items = [
        ("茶水间", "place", 1.5, 2.3, "茶水间，放置饮水机", [(1, 2), (1, 3)]),
        ("会议室A", "place", 3.0, 1.0, "大会议室，可容纳10人", [(3, 1)]),
        ("前台", "place", 0.0, 0.0, "公司前台入口", [(0, 0)]),
        ("充电桩", "place", -1.0, 2.0, "机器人充电区域", [(-1, 2)]),
        ("工具柜", "place", 2.5, 4.0, "存放工具和备件", [(2.5, 4)]),
        ("咖啡杯", "object", 1.6, 2.4, "茶水间桌上的咖啡杯", []),
        ("打印机", "object", 3.1, 1.1, "会议室A旁边的打印机", []),
        ("工作台", "object", 2.0, 3.0, "研发工作台区域", []),
    ]
    for name, mtype, x, y, note, extras in demo_items:
        if mtype == "place":
            req = PlaceMemoryUpsertRequest(
                place_name=name,
                robot_id="demo_robot",
                robot_type="humanoid",
                place_pose=Pose(x=x, y=y, z=0, yaw=0),
                alias=[],
                note=note,
            )
            result = svc.upsert_place(req)
            # 如果有额外位置也写入
            for ex, ey in extras:
                req2 = PlaceMemoryUpsertRequest(
                    place_name=name,
                    robot_id="demo_robot",
                    robot_type="humanoid",
                    place_pose=Pose(x=ex, y=ey, z=0, yaw=0),
                    alias=[],
                    note=f"{note}（额外出口）",
                )
                svc.upsert_place(req2)
        else:
            req = ObjectMemoryUpsertRequest(
                object_name=name,
                robot_id="demo_robot",
                robot_type="humanoid",
                robot_pose=Pose(x=0, y=0, z=0),
                object_pose=Pose(x=x, y=y, z=0.8),
                detect_confidence=0.92,
                image=make_test_image(),
                note=note,
            )
            result = svc.upsert_object(req)

    return f"演示数据写入完成，共 {len(demo_items)} 个类别"


def cmd_delete(svc: MemoryService, memory_id: str) -> str:
    # 直接从 DB 删除
    import sqlite3
    data_dir = svc.store.db_path.parent
    conn = sqlite3.connect(data_dir / "memory_hub.db")
    cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    if affected > 0:
        return f"已删除 id={memory_id}"
    return f"未找到 id={memory_id}，无删除"


def run_interactive(data_dir: Path) -> None:
    svc = make_service(data_dir)
    print("=" * 60)
    print("SpatialMemory 交互式测试")
    print(f"数据目录: {data_dir}")
    print("输入 help 查看命令，输入 quit 退出")
    print("=" * 60)

    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见!")
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()
        args = parts[1:]

        try:
            if cmd in ("quit", "exit"):
                print("再见!")
                break

            if cmd == "help":
                print(HELP_TEXT)
                continue

            if cmd == "health":
                print(cmd_health(svc))
                continue

            if cmd == "list":
                mtype = args[0] if args else "all"
                print(cmd_list(svc, mtype))
                continue

            if cmd == "list-all":
                print(cmd_list(svc, "all"))
                continue

            if cmd == "search":
                if not args:
                    print("用法: search <关键词>")
                    continue
                print(cmd_search(svc, " ".join(args)))
                continue

            if cmd == "find":
                if not args:
                    print("用法: find <名称>")
                    continue
                print(cmd_find(svc, " ".join(args)))
                continue

            if cmd == "near":
                if len(args) < 2:
                    print("用法: near <x> <y> [radius]")
                    continue
                x, y = float(args[0]), float(args[1])
                radius = float(args[2]) if len(args) > 2 else 2.0
                print(cmd_near(svc, x, y, radius))
                continue

            if cmd == "add-object":
                name = args[0] if args else input("  对象名称: ").strip()
                x = float(args[1]) if len(args) > 1 else float(input("  x坐标: "))
                y = float(args[2]) if len(args) > 2 else float(input("  y坐标: "))
                print(cmd_add_object(svc, name, x, y))
                continue

            if cmd == "add-place":
                name = args[0] if args else input("  地点名称: ").strip()
                x = float(args[1]) if len(args) > 1 else float(input("  x坐标: "))
                y = float(args[2]) if len(args) > 2 else float(input("  y坐标: "))
                print(cmd_add_place(svc, name, x, y))
                continue

            if cmd == "add-semantic":
                note = " ".join(args) if args else input("  描述: ").strip()
                print(cmd_add_semantic(svc, note))
                continue

            if cmd == "delete":
                if not args:
                    print("用法: delete <id>")
                    continue
                print(cmd_delete(svc, args[0]))
                continue

            if cmd == "demo":
                print(cmd_demo(svc))
                continue

            print(f"未知命令: {cmd}，输入 help 查看可用命令")

        except Exception as e:
            print(f"[错误] {e}")


# ---------------------------------------------------------------------------
# 命令行快速执行
# ---------------------------------------------------------------------------

QUICK_CMDS = {
    "find-tearoom": lambda svc: cmd_find(svc, "茶水间"),
    "list-places": lambda svc: cmd_list(svc, "place"),
    "list-objects": lambda svc: cmd_list(svc, "object"),
    "list-all": lambda svc: cmd_list(svc, "all"),
    "demo": lambda svc: cmd_demo(svc),
    "health": lambda svc: cmd_health(svc),
}


def run_quick(data_dir: Path, cmd: str, rest: list[str]) -> None:
    svc = make_service(data_dir)

    if cmd in QUICK_CMDS:
        print(QUICK_CMDS[cmd](svc))
        return

    if cmd == "find":
        if not rest:
            print("用法: --cmd find <名称>")
            return
        print(cmd_find(svc, " ".join(rest)))
        return

    if cmd == "near":
        if len(rest) < 2:
            print("用法: --cmd near <x> <y> [radius]")
            return
        x, y = float(rest[0]), float(rest[1])
        radius = float(rest[2]) if len(rest) > 2 else 2.0
        print(cmd_near(svc, x, y, radius))
        return

    if cmd == "search":
        if not rest:
            print("用法: --cmd search <关键词>")
            return
        print(cmd_search(svc, " ".join(rest)))
        return

    if cmd == "list":
        print(cmd_list(svc, rest[0] if rest else "all"))
        return

    if cmd == "add-place":
        if len(rest) < 3:
            print("用法: --cmd add-place <名称> <x> <y>")
            return
        print(cmd_add_place(svc, rest[0], float(rest[1]), float(rest[2])))
        return

    if cmd == "add-object":
        if len(rest) < 3:
            print("用法: --cmd add-object <名称> <x> <y>")
            return
        print(cmd_add_object(svc, rest[0], float(rest[1]), float(rest[2])))
        return

    print(f"未知命令: {cmd}")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SpatialMemory 测试脚本")
    parser.add_argument(
        "--data-dir", "-d",
        help="数据目录 (默认: ./data)",
    )
    parser.add_argument(
        "--cmd", "-c",
        help="直接执行命令 (非交互模式)，可用命令: find find-tearoom list list-all "
             "list-places list-objects near search add-place add-object demo health",
    )
    parser.add_argument("args", nargs="*", help="命令参数")
    ns = parser.parse_args()

    data_dir = resolve_data_dir(ns.data_dir)

    if ns.cmd:
        run_quick(data_dir, ns.cmd, ns.args)
    else:
        run_interactive(data_dir)


if __name__ == "__main__":
    main()
