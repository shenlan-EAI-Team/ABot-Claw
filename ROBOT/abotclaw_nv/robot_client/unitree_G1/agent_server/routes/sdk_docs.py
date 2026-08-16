"""Auto-generated SDK documentation endpoint for Unitree G1.

Parses g1_sdk.py via AST to extract method signatures and docstrings.
"""

from __future__ import annotations

import ast
import logging
import os
import textwrap

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code", tags=["code"])

_SDK_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "robot_sdk")


def _parse_class_from_file(filepath: str, class_name: str) -> dict:
    """Parse a Python file with AST and extract public methods from a class."""
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return _extract_class_info(node)

    return {"docstring": "", "methods": {}}


def _is_property(item: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in item.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "property":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == "property":
            return True
    return False


def _extract_class_info(cls_node: ast.ClassDef) -> dict:
    """Extract docstring and public methods from an AST ClassDef node."""
    docstring = ast.get_docstring(cls_node) or ""
    methods = {}

    for item in cls_node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name.startswith("_"):
            continue
        if _is_property(item):
            continue

        # Build signature string from args
        sig = _build_signature(item)
        doc = ast.get_docstring(item) or ""

        methods[item.name] = {
            "signature": sig,
            "docstring": doc,
        }

    return {"docstring": docstring, "methods": methods}


def _build_signature(func_node: ast.FunctionDef) -> str:
    """Build a human-readable signature string from a FunctionDef AST node."""
    args = func_node.args
    parts = []

    # Positional args (skip 'self')
    all_args = [a.arg for a in args.args]
    defaults = [None] * (len(all_args) - len(args.defaults)) + list(args.defaults)

    for arg_name, default in zip(all_args, defaults):
        if arg_name == "self":
            continue
        if default is not None:
            default_str = _ast_value_to_str(default)
            parts.append(f"{arg_name}={default_str}")
        else:
            parts.append(arg_name)

    return f"({', '.join(parts)})"


def _ast_value_to_str(node: ast.expr) -> str:
    """Convert an AST default value node to a readable string."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, (ast.List, ast.Tuple)):
        elts = ", ".join(_ast_value_to_str(e) for e in node.elts)
        if isinstance(node, ast.List):
            return f"[{elts}]"
        return f"({elts})"
    if isinstance(node, ast.Dict):
        return "{}"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return f"-{_ast_value_to_str(node.operand)}"
    if isinstance(node, ast.Attribute):
        return f"{_ast_value_to_str(node.value)}.{node.attr}"
    if isinstance(node, ast.NameConstant):
        return repr(node.value)
    return "..."


def generate_sdk_docs() -> dict:
    """Generate SDK documentation by parsing SDK source files via AST."""
    docs = {
        "version": "1.0.0",
        "description": (
            "Unitree G1 Humanoid Robot SDK. "
            "The `/code/execute` wrapper builds `G1RobotEnv.from_config()` as `env` "
            "(same role as Piper's `env`) and installs aliases: "
            "`grasp_target`, `grasp_something`, `grasp_with_vlac`, `release_object`, `camera` (G1D455Camera, ZMQ), `camera_d435i` (TCP, JPEG+Z16), "
            "`yolo` (YoloSDK, HTTP), `memory`, `face`, `tts`, `Pose` (memory_sdk.Pose helper), "
            "plus `Nav2Anywhere` (ROS1 Noetic navigation client class). "
            "Robot IPs and HTTP URLs come from `robot_sdk/config.yaml` (`ROBOT_SDK_CONFIG` optional). "
            "No import needed in submitted code (except ROS1 types for navigation: `geometry_msgs`). "
            "Low-level IK is not exposed; use `grasp_something(name)` (一句话：检测+抓取), "
            "`grasp_target(right_pos, left_pos)` (自带坐标的精细抓取), "
            "`release_object()` (放下/松手) for fixed-pattern manipulation."
        ),
        "modules": {},
        "usage": {
            "example": '''# All instances are pre-created — no import needed
# Prefer env.* (G1RobotEnv); top-level names are backward-compatible aliases.

# ---- Cameras (Piper-shaped) ----
imgs, ts = env.read_cameras()
print("keys:", list(imgs.keys()))

# ---- One-shot grasp (grasp_something, 推荐) ----
# 一句话完成 YOLO 检测 + 坐标换算 + 机械臂抓取，失败返回 False，原因看 stdout。
# 默认按置信度排序取第一个；多目标时可传 detection_index=n。
ok = grasp_something("bottle")
print(f"grasp_something: {ok}")

# ---- Fine-grained grasp (grasp_target, wraps IK internally) ----
# 需要自己控制目标挑选策略（例如"挑最近"而非"挑置信度最高"）时用这个。
# right_pos 必须是 base 坐标，一般从 yolo.segment_3d 的 position_base 里取。
detections = yolo.segment_3d("bottle")
if detections:
    target = min(detections, key=lambda d: d["depth_m"])
    ok = grasp_target(
        right_pos=target["position_base"],
        left_pos=[-0.003, 0.212, -0.004],
    )
    print(f"Grasp result: {ok}")

# ---- Release (release_object) ----
# 抓取之后松手放下：回 home + 打开灵巧手。预注入，无需 import。
release_object()

# ---- Camera (camera, ZMQ) ----
# get_frame() 返回 (rgb, depth) 两个 ndarray；取不到都是 None
rgb, depth = camera.get_frame()
if rgb is not None:
    print(f"RGB shape: {rgb.shape}, "
          f"depth shape: {depth.shape if depth is not None else None}")

# ---- Object detection (yolo, HTTP API) ----
# export YOLO_URL=http://127.0.0.1:8013/detect  # G1_Yolo (Ultralytics)
# 无 D435i 时仅 HTTP：YoloSDK(require_camera=False); dets = yolo.detect_on_rgb(your_rgb_hw3)
labels = yolo.detect_env()
detections = yolo.segment_3d("bottle")
for d in detections:
    print(f"{d['label']}: base={d['position_base']}, depth={d['depth_m']:.3f}m")
path = yolo.save_detection_image()  # annotated JPEG -> ~/d435i_yolo_*.jpg
print("saved:", path)

# ---- Face recognition (face, HTTP API + ZMQ) ----
# Initialize and recognize faces
face.start()
people = face.list_people()  # List enrolled people
result = face.recognize_current_frame()  # Recognize current frame
for match in result.get('results', []):
    print(f"Recognized: {match['name']} ({match['match_score']:.2%})")

# ---- Text-to-Speech (tts, DDS) ----
# Initialize and speak (tts is pre-created, no import needed)
tts.initialize()  # Must call before first use
tts.speak("你好，我是G1机器人")
volume = tts.get_volume()
print(f"Current volume: {volume}")

# ---- Navigation (Nav2Anywhere, ROS1 Noetic) ----
# HTTP alternative: POST /nav/to_pose
from robot_sdk.navigation_sdk import Nav2Anywhere
nav = Nav2Anywhere()
# 方式 1：传 yaw 角（推荐，move_base 规划最通畅）
reached = nav.publish_simple_goal(x=1.0, y=0.0, yaw=1.57) and nav.wait_until_reached(timeout_sec=120.0)
# 方式 2：nav_to_pose 传四元数，SDK 内部自动清理 X/Y 分量后发布（qx/qy 不要求为 0）
from geometry_msgs.msg import PoseStamped
goal = PoseStamped()
goal.header.frame_id = "map"
goal.pose.position.x = 1.0
goal.pose.position.y = 2.0
goal.pose.orientation.z = -0.354
goal.pose.orientation.w = 0.935
nav.nav_to_pose(goal)
reached = nav.wait_until_reached(timeout_sec=180.0)
''',
            "notes": [
                "Pre-created: `env` (G1RobotEnv) plus aliases `grasp_target`, `grasp_something`, `grasp_with_vlac`, `release_object`, `camera`, `camera_d435i`, `yolo`, `vlac`, `memory`, `face`, `tts`, `Pose` (memory_sdk.Pose), and class `Nav2Anywhere` (ROS1 Noetic)",
                "camera / camera_d435i: `get_frame()` 返回 `(rgb, depth)` tuple（都是 np.ndarray 或 None）。用法：`rgb, depth = camera.get_frame()`；`rgb is None` 才算失败。",
                "GET /cameras lists `d455` and `d435i` with `available` / `transport`",
                "yolo calls G1_Yolo HTTP service (YOLO_URL, default :8013); save_detection_image() writes ~/d435i_yolo_*.jpg",
                "memory calls Spatial Memory Hub HTTP API for object memories",
                "face: FaceSDK via HTTP (see `face.url` in robot_sdk/config.yaml) + D455 ZMQ; call face.start() before use",
                "tts: TTSClient via DDS; call tts.initialize() before first use; requires LocoClient activation internally; NO import needed - use pre-created `tts` instance",
                "Nav2Anywhere: ROS1 Noetic navigation client (rospy; publishes to /move_base_simple/goal); recommended: `nav.publish_simple_goal(x, y, yaw=angle)` (yaw angle avoids quaternion X/Y issues); also accepts `qx/qy/qz/qw` quaternion args (SDK auto-converts to clean Z-axis-only quaternion before publishing); `wait_until_reached(timeout)` blocks until position arrives (3-phase: move_base nav → fine yaw rotate → fine XY adjust); `nav_to_pose(pose_stamped)` for PoseStamped-style goals with auto quaternion cleaning; `rotate_to_yaw(target_yaw)` for in-place fine-tuning; `nav_to_with_yaw(x, y, target_yaw)` for nav+orientation in one call; default thresholds: REACH_THRESHOLD=0.2m, YAW_THRESHOLD_DEFAULT=0.2rad, FINE_YAW_THRESHOLD=0.15rad, XY_FINE_THRESHOLD=0.15m, XY_FINE_SPEED=0.2m/s, ROTATE_SPEED_DEFAULT=0.4rad/s; G1 rotation limit ~±1.3rad; or HTTP `POST /nav/to_pose` from outside /code/execute",
                "All methods are synchronous (blocking)",
                "Fixed-pattern manipulation (pre-injected, no import): `grasp_something(name)` 一句话检测+抓取（内部顺序调用 YOLO → grasp_target，按置信度取第一个，失败返回 False）、`grasp_with_vlac(name, task_description=...)` 在共享 D435i Before/After 上返回 execution_success/reward/done、`grasp_target(r_pos, l_pos)` 给好坐标的精细抓取、`release_object()` 放下/松手（回 home + 打开灵巧手）；direct `ik` / IK SDK imports are rejected",
            ],
        },
    }

    # --- env (G1RobotEnv) ---
    g1_env_path = os.path.join(_SDK_DIR, "g1_robot_env.py")
    if os.path.exists(g1_env_path):
        class_info = _parse_class_from_file(g1_env_path, "G1RobotEnv")
        docs["modules"]["env (G1RobotEnv)"] = {
            "import": "# pre-created as `env`",
            "description": (
                "Aggregates D455, D435i (lazy), YoloSDK, MemorySDK, Face, TTS, grasp_target. "
                "``read_cameras()`` returns the same (images, timestamps) tuple shape as Piper's "
                "``PiperRobotEnv.read_cameras`` (keys include ``d455_rgb`` / ``d455_depth``; optional ``d435i_*``)."
            ),
            "environment": "/usr/bin/python3",
            **class_info,
        }

    # --- yolo (YoloSDK) ---
    yolo_sdk_path = os.path.join(_SDK_DIR, "yolo_sdk.py")
    if os.path.exists(yolo_sdk_path):
        class_info = _parse_class_from_file(yolo_sdk_path, "YoloSDK")
        docs["modules"]["yolo (YoloSDK)"] = {
            "import": "# pre-created as `yolo`",
            "description": (
                "YOLO HTTP detect + G1 D435i TCP RGB-D: ``detect_env`` / ``segment_3d`` / "
                "``save_detection_image`` use ``G1D435iCamera`` (JPEG+Z16). Set ``YOLO_URL`` "
                "(e.g. G1_Yolo ``http://127.0.0.1:8013/detect``); optional ``YOLO_G1_TORSO_OFFSET_*`` for base offset."
            ),
            "environment": "/usr/bin/python3 (httpx + OpenCV)",
            **class_info,
        }

    # --- memory (MemorySDK) ---
    memory_sdk_path = os.path.join(_SDK_DIR, "memory_sdk.py")
    if os.path.exists(memory_sdk_path):
        class_info = _parse_class_from_file(memory_sdk_path, "MemorySDK")
        docs["modules"]["memory (MemorySDK)"] = {
            "import": "# pre-created as `memory`",
            "description": "Spatial Memory Hub object memory upsert + name-based query",
            "environment": "/usr/bin/python3 (HTTP API)",
            **class_info,
        }

    # --- face (FaceSDK) ---
    face_sdk_path = os.path.join(_SDK_DIR, "face_sdk.py")
    if os.path.exists(face_sdk_path):
        class_info = _parse_class_from_file(face_sdk_path, "FaceSDK")
        docs["modules"]["face (FaceSDK)"] = {
            "import": "# pre-created as `face`",
            "description": "Face recognition via HTTP API (URL from config.yaml `face`) + D455 ZMQ camera",
            "environment": "/usr/bin/python3 (HTTP API + ZMQ)",
            **class_info,
        }

    # --- tts (TTSClient) ---
    tts_sdk_path = os.path.join(_SDK_DIR, "tts_sdk.py")
    if os.path.exists(tts_sdk_path):
        class_info = _parse_class_from_file(tts_sdk_path, "TTSClient")
        docs["modules"]["tts (TTSClient)"] = {
            "import": "# pre-created as `tts`",
            "description": "Text-to-Speech via DDS (requires LocoClient activation)",
            "environment": "/usr/bin/python3 (DDS)",
            **class_info,
        }

    # --- Nav2Anywhere (navigation_sdk) ---
    nav_sdk_path = os.path.join(_SDK_DIR, "navigation_sdk.py")
    if os.path.exists(nav_sdk_path):
        class_info = _parse_class_from_file(nav_sdk_path, "Nav2Anywhere")
        docs["modules"]["Nav2Anywhere (navigation_sdk)"] = {
            "import": "# pre-injected as `Nav2Anywhere` (class; ros1 noetic)",
            "description": (
                "ROS1 Noetic navigation client: subscribes ``/tf``, publishes ``move_base`` action goal. "
                "HTTP equivalent: ``POST /nav/to_pose``."
            ),
            "environment": "/usr/bin/python3 (ROS1 Noetic + rospy)",
            **class_info,
        }

    docs["constants"] = {
        "g1_config": {
            "arm_joints": 8,
            "body_joints": 15,
            "description": "8 arm joints (4 per arm) + 15 body joints (waist + legs)",
        },
        "velocity_limits": {
            "max_vx": 1.0,
            "max_vy": 0.5,
            "max_vyaw": 1.0,
            "description": "Maximum walking velocities (m/s and rad/s)",
        },
        "body_height_limits": {
            "min": 0.5,
            "max": 0.8,
            "description": "Body height range in meters",
        },
        "network": {
            "default_interface": os.environ.get("G1_ARM_NETWORK_INTERFACE", os.environ.get("UNITREE_IFACE", "enp4s0")),
            "description": "Default network interface for G1 communication",
        },
        "environments": {
            "robot_control": "/usr/bin/python3 (Unitree SDK2)",
            "perception": "HTTP YOLO service + optional G1 D435i TCP stream on robot (see config.yaml `g1`)",
            "navigation": "ROS2 Nav2 client (Nav2Anywhere) or HTTP POST /nav/to_pose",
            "description": "Robot control via Unitree SDK2; perception via YOLO HTTP and D435i TCP where enabled; navigation via ROS2 or HTTP",
        },
    }

    return docs


@router.get("/sdk")
async def get_sdk_documentation():
    """Get auto-generated SDK documentation.

    Returns documentation for all available SDK modules, methods, and their
    signatures. This is generated by introspecting the actual code, so it's
    always accurate.

    No lease required.
    """
    return generate_sdk_docs()


@router.get("/sdk/modules")
async def get_sdk_modules():
    """Get quick list of all available SDK modules.

    Returns a simple list of module names and their pre-created instance names.
    Use this for quick discovery before diving into full documentation.

    No lease required.
    """
    return {
        "modules": [
            {"name": "env", "type": "G1RobotEnv", "description": "Aggregated runtime (from_config); use env.camera, env.yolo, …"},
            {"name": "grasp_something", "type": "function", "description": "One-shot: YOLO detect(name) + grasp_target. Signature: grasp_something(name, *, robot_ip=None, detection_index=0, right_target_offset=None, log_dir=None) -> bool. 失败返回 False，原因看 stdout。"},
            {"name": "grasp_with_vlac", "type": "function", "description": "One-shot grasp + shared D435i Before/After + VLAC critic/verification. Returns execution_success, reward, done; VLAC failure is non-fatal to grasp execution."},
            {"name": "grasp_target", "type": "function", "description": "Fixed-pattern arm grasp (wraps IK internally); 需要显式给 right_pos/left_pos base 坐标"},
            {"name": "release_object", "type": "function", "description": "Release grasped object: home + open dexterous hand"},
            {"name": "Pose", "type": "class (memory_sdk)", "description": "Lightweight pose helper for memory upsert/query (x, y, z, yaw, ...)"},
            {"name": "camera", "type": "G1D455Camera", "description": "D455 head camera via ZMQ (RGB-D)"},
            {"name": "camera_d435i", "type": "G1D435iCamera", "description": "D435i camera via TCP port 8765 (lazy init)"},
            {"name": "yolo", "type": "YoloSDK", "description": "Object detection via HTTP API (:8013)"},
            {"name": "memory", "type": "MemorySDK", "description": "Spatial Memory Hub via HTTP API (:8022)"},
            {"name": "face", "type": "FaceSDK", "description": "Face recognition via HTTP API (:8016) + ZMQ"},
            {"name": "tts", "type": "TTSClient", "description": "Text-to-Speech via DDS (call tts.initialize() before use)"},
            {"name": "Nav2Anywhere", "type": "class (navigation_sdk)", "description": "ROS1 nav client (rospy, move_base); or use POST /nav/to_pose"},
        ],
        "note": "All instances are pre-created in /code/execute environment. No import needed.",
        "documentation": "/code/sdk or /code/sdk/markdown for full docs",
        "security_note": "Direct network access (requests, httpx, urllib) is blocked in /code/execute. Use pre-created SDK instances (yolo, memory, face) for HTTP API access.",
    }


@router.get("/sdk/markdown", response_class=HTMLResponse)
async def get_sdk_markdown():
    """Get SDK documentation as rendered HTML.

    Opens nicely in a browser. Also usable by agents via curl.

    No lease required.
    """
    docs = generate_sdk_docs()

    md = f"# Robot SDK Documentation\n\n"
    md += f"**Version:** {docs['version']}\n\n"
    md += f"{docs['description']}\n\n"

    # Usage
    md += "## Quick Start\n\n"
    md += "```python\n"
    md += docs["usage"]["example"]
    md += "```\n\n"

    md += "**Notes:**\n"
    for note in docs["usage"]["notes"]:
        md += f"- {note}\n"
    md += "\n"

    # Modules
    md += "## Modules\n\n"
    for module_name, module_info in docs.get("modules", {}).items():
        md += f"### `{module_name}`\n\n"
        md += f"**Import:** `{module_info['import']}`\n\n"
        md += f"{module_info['description']}\n\n"

        if module_info.get("docstring"):
            md += f"{module_info['docstring']}\n\n"

        md += "**Methods:**\n\n"
        for method_name, method_info in module_info.get("methods", {}).items():
            sig = method_info.get("signature", "()")
            md += f"#### `{method_name}{sig}`\n\n"
            if method_info.get("docstring"):
                md += f"{method_info['docstring']}\n\n"

    # Constants
    if "constants" in docs:
        md += "## Constants\n\n"
        for const_name, const_info in docs["constants"].items():
            md += f"### {const_name}\n\n"
            if isinstance(const_info, dict):
                if "description" in const_info:
                    md += f"{const_info['description']}\n\n"
                for k, v in const_info.items():
                    if k != "description":
                        md += f"- `{k}`: {v}\n"
            md += "\n"

    # Render markdown to HTML using zero-dependency approach
    import html as html_mod
    import re

    raw_md = md

    # Convert markdown to HTML (lightweight, no external deps)
    lines = raw_md.split("\n")
    html_lines = []
    in_code_block = False
    in_list = False

    for line in lines:
        if line.startswith("```"):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            if in_code_block:
                html_lines.append("</code></pre>")
                in_code_block = False
            else:
                lang = line[3:].strip()
                html_lines.append(f'<pre><code class="language-{lang}">')
                in_code_block = True
            continue

        if in_code_block:
            html_lines.append(html_mod.escape(line))
            continue

        stripped = line.strip()

        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
            continue

        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = stripped[2:]
            # Inline code
            content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
            html_lines.append(f"<li>{content}</li>")
            continue

        if in_list:
            html_lines.append("</ul>")
            in_list = False

        if stripped.startswith("#### "):
            content = stripped[5:]
            content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
            html_lines.append(f"<h4>{content}</h4>")
        elif stripped.startswith("### "):
            content = stripped[4:]
            content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
            html_lines.append(f"<h3>{content}</h3>")
        elif stripped.startswith("## "):
            content = stripped[3:]
            html_lines.append(f"<h2>{content}</h2>")
        elif stripped.startswith("# "):
            content = stripped[2:]
            html_lines.append(f"<h1>{content}</h1>")
        else:
            content = stripped
            content = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', content)
            content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
            html_lines.append(f"<p>{content}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_code_block:
        html_lines.append("</code></pre>")

    body = "\n".join(html_lines)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robot SDK Documentation</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.6; color: #24292e; }}
  h1 {{ border-bottom: 2px solid #e1e4e8; padding-bottom: 0.3em; }}
  h2 {{ border-bottom: 1px solid #e1e4e8; padding-bottom: 0.3em; margin-top: 2em; }}
  h3 {{ margin-top: 1.5em; }}
  h4 {{ margin-top: 1em; color: #0366d6; }}
  pre {{ background: #f6f8fa; border-radius: 6px; padding: 16px; overflow-x: auto; }}
  code {{ font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace; font-size: 0.9em; }}
  p > code, li > code, h3 > code, h4 > code {{ background: #f0f0f0; padding: 0.2em 0.4em; border-radius: 3px; }}
  ul {{ padding-left: 1.5em; }}
  li {{ margin: 0.25em 0; }}
  strong {{ font-weight: 600; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
