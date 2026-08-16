from __future__ import annotations

import base64
import io
import os
import sys
import time
from typing import Any

import requests
from PIL import Image


BASE = os.getenv("SPATIAL_MEMORY_HUB_URL", "http://127.0.0.1:8022")
TIMEOUT = 10


def tiny_image_b64() -> str:
    img = Image.new("RGB", (16, 16), color=(220, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def local_image_path(returned_path: str) -> str:
    """Resolve container paths when this smoke test runs on the bind-mounted host."""
    container_root = "/services/SpatialMemory"
    if returned_path.startswith(container_root):
        return os.path.dirname(os.path.abspath(__file__)) + returned_path[len(container_root):]
    return returned_path


def must_post(path: str, payload: dict[str, Any], timeout: int = TIMEOUT) -> dict[str, Any]:
    resp = requests.post(f"{BASE}{path}", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def must_get(path: str, timeout: int = TIMEOUT) -> dict[str, Any]:
    resp = requests.get(f"{BASE}{path}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def print_step(name: str) -> None:
    print(f"\n[STEP] {name}")


def print_ok(name: str, data: Any) -> None:
    print(f"[OK] {name}: {data}")


def main() -> None:
    failures: list[str] = []
    image_b64 = tiny_image_b64()
    ts_seed = int(time.time())
    object_name = f"cup_smoke_{ts_seed}"
    place_name = f"kitchen_smoke_{ts_seed}"
    place_image_name = f"kitchen_visual_smoke_{ts_seed}"
    task_marker = f"smoke_task_{ts_seed}"

    object_id = None
    semantic_id = None

    checks = [
        "health",
        "object upsert",
        "place upsert",
        "place upsert base64 image",
        "place upsert data uri image",
        "place invalid image",
        "semantic ingest",
        "keyframe batch ingest",
        "query object",
        "query place",
        "query position",
        "query semantic text",
        "query unified",
        "pipeline task create",
        "pipeline task status",
    ]

    for item in checks:
        try:
            if item == "health":
                print_step(item)
                data = must_get("/health")
                check(data.get("status") == "ok", "health status must be ok")
                print_ok(item, {"status": data.get("status"), "records": data.get("records")})

            elif item == "object upsert":
                print_step(item)
                payload = {
                    "object_name": object_name,
                    "robot_id": "humanoid_001",
                    "robot_type": "humanoid",
                    "robot_pose": {
                        "x": 1.0,
                        "y": 1.0,
                        "z": 0.0,
                        "roll": 0.0,
                        "pitch": 0.0,
                        "yaw": 0.1,
                        "frame_id": "map",
                    },
                    "object_pose": {
                        "x": 1.2,
                        "y": 1.1,
                        "z": 0.8,
                        "roll": 0.0,
                        "pitch": 0.0,
                        "yaw": 0.0,
                        "frame_id": "map",
                    },
                    "detect_confidence": 0.92,
                    "image": image_b64,
                }
                data = must_post("/memory/object/upsert", payload)
                check(data.get("ok") is True, "object upsert must return ok=true")
                object_id = data.get("id")
                check(bool(object_id), "object upsert must return id")
                print_ok(item, data)

            elif item == "place upsert":
                print_step(item)
                payload = {
                    "place_name": place_name,
                    "robot_id": "humanoid_001",
                    "robot_type": "humanoid",
                    "place_pose": {
                        "x": 2.0,
                        "y": 3.0,
                        "z": 0.0,
                        "roll": 0.0,
                        "pitch": 0.0,
                        "yaw": 1.57,
                        "frame_id": "map",
                    },
                    "alias": ["kitchen"],
                    "note": "smoke test place",
                }
                data = must_post("/memory/place/upsert", payload)
                check(data.get("ok") is True, "place upsert must return ok=true")
                check(bool(data.get("id")), "place upsert must return id")
                check(data.get("has_reference_image") is False, "place without image must report no reference image")
                check(data.get("image_path") is None, "place without image must return image_path=null")
                print_ok(item, data)

            elif item in {"place upsert base64 image", "place upsert data uri image"}:
                print_step(item)
                captured_at = time.time()
                is_data_uri = item == "place upsert data uri image"
                payload = {
                    "place_name": place_image_name,
                    "robot_id": "humanoid_001",
                    "robot_type": "humanoid",
                    "place_pose": {"x": 2.1, "y": 3.1},
                    "image": f"data:image/jpeg;base64,{image_b64}" if is_data_uri else image_b64,
                    "image_captured_at": captured_at if not is_data_uri else None,
                    "task_description": "smoke reference scene",
                }
                data = must_post("/memory/place/upsert", payload)
                check(data.get("ok") is True, "place image upsert must return ok=true")
                check(data.get("has_reference_image") is True, "place image upsert must report a reference image")
                image_path = data.get("image_path")
                check(bool(image_path), "place image upsert must return image_path")
                saved_path = local_image_path(image_path)
                check(os.path.isfile(saved_path), f"saved reference image does not exist: {saved_path}")
                with Image.open(saved_path) as saved_image:
                    saved_image.verify()
                print_ok(item, data)

            elif item == "place invalid image":
                print_step(item)
                response = requests.post(
                    f"{BASE}/memory/place/upsert",
                    json={
                        "place_name": f"invalid_image_{ts_seed}",
                        "robot_id": "humanoid_001",
                        "robot_type": "humanoid",
                        "place_pose": {"x": 0.0, "y": 0.0},
                        "image": "this-is-not-a-valid-image",
                    },
                    timeout=TIMEOUT,
                )
                check(response.status_code == 422, f"invalid place image must return 422, got {response.status_code}")
                health = must_get("/health")
                check(health.get("status") == "ok", "service must remain healthy after invalid image")
                print_ok(item, {"status_code": response.status_code})

            elif item == "semantic ingest":
                print_step(item)
                payload = {
                    "robot_id": "dog_001",
                    "robot_type": "robot_dog",
                    "robot_pose": {
                        "x": 2.0,
                        "y": -0.5,
                        "z": 0.0,
                        "roll": 0.0,
                        "pitch": 0.0,
                        "yaw": 1.2,
                        "frame_id": "map",
                    },
                    "image": image_b64,
                    "note": f"{object_name} near table",
                    "tags": ["smoke"],
                }
                data = must_post("/memory/semantic/ingest", payload)
                check(data.get("ok") is True, "semantic ingest must return ok=true")
                semantic_id = data.get("id")
                check(bool(semantic_id), "semantic ingest must return id")
                print_ok(item, data)

            elif item == "keyframe batch ingest":
                print_step(item)
                payload = {
                    "task_id": task_marker,
                    "items": [
                        {
                            "camera_source": "front_camera",
                            "rank": 1,
                            "score": 0.88,
                            "timestamp": time.time(),
                            "timestamp_ns": int(time.time() * 1e9),
                            "robot_id": "dog_001",
                            "robot_type": "robot_dog",
                            "pose": {
                                "x": 3.0,
                                "y": 1.5,
                                "z": 0.0,
                                "roll": 0.0,
                                "pitch": 0.0,
                                "yaw": 0.3,
                                "frame_id": "map",
                            },
                            "note": "smoke keyframe",
                            "image": image_b64,
                        }
                    ],
                }
                data = must_post("/memory/keyframe/ingest-batch", payload)
                check(data.get("ok") is True, "keyframe batch ingest must return ok=true")
                check(data.get("success", 0) >= 1, "keyframe batch ingest success must be >= 1")
                print_ok(item, data)

            elif item == "query object":
                print_step(item)
                data = must_post("/query/object", {"name": object_name, "n_results": 5})
                results = data.get("results", [])
                check(len(results) >= 1, "query object should return at least one result")
                names = [r.get("name", "") for r in results]
                check(any(object_name in n for n in names), "query object should contain inserted object name")
                print_ok(item, {"count": len(results)})

            elif item == "query place":
                print_step(item)
                data = must_post("/query/place", {"name": place_name, "n_results": 5})
                results = data.get("results", [])
                check(len(results) >= 1, "query place should return at least one result")
                names = [r.get("name", "") for r in results]
                check(any(place_name in n for n in names), "query place should contain inserted place name")
                image_data = must_post(
                    "/query/place",
                    {"name": place_image_name, "robot_id": "humanoid_001", "n_results": 5},
                )
                image_results = image_data.get("results", [])
                check(len(image_results) >= 1, "query place should return the reference-image place")
                evidence = image_results[0].get("evidence", {})
                extra = evidence.get("extra", {})
                check(bool(evidence.get("image_path")), "queried place evidence must contain image_path")
                check(extra.get("task_description") == "smoke reference scene", "queried task_description mismatch")
                check(isinstance(extra.get("image_captured_at"), (int, float)), "queried image_captured_at must be numeric")
                print_ok(item, {"count": len(results)})

            elif item == "query position":
                print_step(item)
                data = must_post(
                    "/query/position",
                    {
                        "x": 1.1,
                        "y": 1.05,
                        "radius": 1.0,
                        "n_results": 10,
                    },
                )
                results = data.get("results", [])
                check(len(results) >= 1, "query position should return at least one nearby result")
                print_ok(item, {"count": len(results)})

            elif item == "query semantic text":
                print_step(item)
                data = must_post("/query/semantic/text", {"text": object_name, "n_results": 5})
                results = data.get("results", [])
                check(len(results) >= 1, "semantic text query should return at least one result")
                print_ok(item, {"count": len(results)})

            elif item == "query unified":
                print_step(item)
                data = must_post(
                    "/query/unified",
                    {
                        "text": object_name,
                        "memory_type": "semantic_frame",
                        "n_results": 5,
                    },
                )
                results = data.get("results", [])
                check(len(results) >= 1, "unified query should return at least one result")
                print_ok(item, {"count": len(results)})

            elif item == "pipeline task create":
                print_step(item)
                data = must_post(
                    "/pipeline/tasks",
                    {
                        "task_name": "offline_keyframe_pipeline",
                        "input_uri": "file:///tmp/smoke_dummy.bag",
                        "robot_id": "dog_001",
                        "robot_type": "robot_dog",
                        "options": {"smoke": True},
                    },
                )
                check(bool(data.get("task_id")), "pipeline task create should return task_id")
                print_ok(item, data)
                pipeline_task_id = data["task_id"]

            elif item == "pipeline task status":
                print_step(item)
                # Poll briefly for transition to running/completed.
                last = {}
                for _ in range(6):
                    last = must_get(f"/pipeline/tasks/{pipeline_task_id}")
                    if last.get("status") in {"running", "completed"}:
                        break
                    time.sleep(0.4)
                check(last.get("status") in {"queued", "running", "completed"}, "invalid task status")
                print_ok(item, {"status": last.get("status"), "progress": last.get("progress")})

        except Exception as exc:
            failures.append(f"{item}: {exc}")
            print(f"[FAIL] {item}: {exc}")

    print("\n========== SMOKE TEST SUMMARY ==========")
    if not failures:
        print("PASS: all checks passed")
        print(f"base={BASE}, object_id={object_id}, semantic_id={semantic_id}")
        return

    print(f"FAIL: {len(failures)} check(s) failed")
    for err in failures:
        print(f" - {err}")
    sys.exit(1)


if __name__ == "__main__":
    main()
