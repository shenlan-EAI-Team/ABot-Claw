from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
from PIL import Image


PROJECT_DIR = Path(__file__).resolve().parents[1]


def reference_image_b64() -> str:
    image = Image.new("RGBA", (23, 17), color=(24, 80, 190, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class PlaceVisualIndexHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="spatial-memory-stage1-")
        cls.data_dir = Path(cls.temp_dir.name)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        env = os.environ.copy()
        env.update(
            {
                "HOST": "127.0.0.1",
                "PORT": str(cls.port),
                "MEMORY_HUB_DATA_DIR": str(cls.data_dir),
            }
        )
        cls.server = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=PROJECT_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            if cls.server.poll() is not None:
                output = cls.server.stdout.read() if cls.server.stdout else ""
                raise RuntimeError(f"test server exited early:\n{output}")
            try:
                if requests.get(f"{cls.base_url}/health", timeout=0.3).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        else:
            raise RuntimeError("test server did not become healthy")

        cls.place_payload = {
            "place_name": "vpr_stage1_test_place",
            "robot_id": "test_robot",
            "robot_type": "test",
            "place_pose": {
                "x": 1.25,
                "y": -2.5,
                "z": 0.75,
                "roll": 0.1,
                "pitch": -0.2,
                "yaw": 1.3,
                "qx": 0.01,
                "qy": 0.02,
                "qz": 0.6,
                "qw": 0.8,
                "frame_id": "map",
            },
            "alias": ["stage-one", "visual-test"],
            "note": "temporary integration test",
            "image": reference_image_b64(),
            "task_description": "verify visual place index stage one",
        }
        response = requests.post(
            f"{cls.base_url}/memory/place/upsert",
            json=cls.place_payload,
            timeout=5,
        )
        response.raise_for_status()
        cls.upsert_result = response.json()
        cls.place_id = cls.upsert_result["place_id"]
        cls.image_bytes = Path(cls.upsert_result["image_path"]).read_bytes()

        object_response = requests.post(
            f"{cls.base_url}/memory/object/upsert",
            json={
                "object_name": "vpr_stage1_test_object",
                "robot_id": "test_robot",
                "robot_type": "test",
                "robot_pose": {"x": 0, "y": 0},
                "object_pose": {"x": 1, "y": 1},
            },
            timeout=5,
        )
        object_response.raise_for_status()
        cls.object_id = object_response.json()["id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=5)
        cls.temp_dir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        expected: int = 200,
        **kwargs: Any,
    ) -> requests.Response:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            timeout=5,
            **kwargs,
        )
        self.assertEqual(expected, response.status_code, response.text)
        return response

    def test_01_upsert_keeps_old_fields_and_adds_final_jpeg_metadata(self) -> None:
        result = self.upsert_result
        self.assertTrue(result["ok"])
        for field in ("id", "has_reference_image", "image_path"):
            self.assertIn(field, result)
        self.assertEqual(result["id"], result["place_id"])
        self.assertEqual(result["image_id"], result["place_id"])
        self.assertRegex(result["image_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(hashlib.sha256(self.image_bytes).hexdigest(), result["image_sha256"])
        self.assertEqual("not_indexed", result["visual_index_status"])
        with Image.open(io.BytesIO(self.image_bytes)) as image:
            self.assertEqual("RGB", image.mode)
            self.assertEqual("JPEG", image.format)

    def test_02_get_place_returns_full_pose_and_default_metadata(self) -> None:
        detail = self.request("GET", f"/memory/place/{self.place_id}").json()
        self.assertEqual(self.place_id, detail["id"])
        self.assertEqual(self.place_id, detail["place_id"])
        self.assertEqual("place", detail["memory_type"])
        for field in ("x", "y", "z", "roll", "pitch", "yaw", "qx", "qy", "qz", "qw", "frame_id"):
            self.assertIn(field, detail["target_pose"])
        self.assertEqual(self.upsert_result["image_path"], detail["evidence"]["image_path"])
        self.assertEqual(self.upsert_result["image_sha256"], detail["image_sha256"])
        self.assertEqual(f"/memory/place/{self.place_id}/image", detail["image_url"])
        self.assertEqual("not_indexed", detail["visual_index"]["status"])
        self.assertEqual(["stage-one", "visual-test"], detail["aliases"])

    def test_03_get_place_rejects_missing_and_non_place_ids(self) -> None:
        self.request("GET", "/memory/place/plc_does_not_exist", expected=404)
        self.request("GET", f"/memory/place/{self.object_id}", expected=404)

    def test_04_image_endpoint_streams_exact_jpeg_with_etag(self) -> None:
        response = self.request("GET", f"/memory/place/{self.place_id}/image")
        self.assertEqual("image/jpeg", response.headers["content-type"])
        self.assertEqual(self.image_bytes, response.content)
        self.assertEqual(f'"{self.upsert_result["image_sha256"]}"', response.headers["etag"])
        self.request("GET", "/memory/place/plc_does_not_exist/image", expected=404)
        self.request("GET", f"/memory/place/{self.object_id}/image", expected=404)

    def test_05_image_endpoint_handles_missing_file_and_path_traversal(self) -> None:
        no_image = self.request(
            "POST",
            "/memory/place/upsert",
            json={
                "place_name": "no_image_place",
                "robot_id": "test_robot",
                "robot_type": "test",
                "place_pose": {"x": 0, "y": 0},
            },
        ).json()
        self.request("GET", f"/memory/place/{no_image['place_id']}/image", expected=404)

        db_path = self.data_dir / "memory_hub.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE memories SET image_path = ? WHERE id = ?",
                ("/etc/passwd", no_image["place_id"]),
            )
        self.request("GET", f"/memory/place/{no_image['place_id']}/image", expected=404)

    def test_06_patch_supports_all_states_and_server_timestamp(self) -> None:
        cases = [
            ("pending", {}),
            (
                "indexed",
                {
                    "image_sha256": self.upsert_result["image_sha256"],
                    "backend": "salad",
                    "version": "salad_v1",
                },
            ),
            ("failed", {"error": "temporary indexing failure"}),
            ("not_indexed", {}),
            ("deleted", {}),
        ]
        for status, extra in cases:
            with self.subTest(status=status):
                result = self.request(
                    "PATCH",
                    f"/memory/place/{self.place_id}/visual-index",
                    json={"status": status, **extra},
                ).json()["visual_index"]
                self.assertEqual(status, result["status"])
                self.assertEqual(self.place_id, result["image_id"])
                self.assertRegex(result["updated_at"], r"Z$")
                if status != "failed":
                    self.assertIsNone(result["error"])

    def test_07_patch_validation_conflict_and_not_found(self) -> None:
        self.request(
            "PATCH",
            f"/memory/place/{self.place_id}/visual-index",
            expected=422,
            json={"status": "unknown"},
        )
        self.request(
            "PATCH",
            f"/memory/place/{self.place_id}/visual-index",
            expected=422,
            json={"status": "pending", "image_sha256": "abc"},
        )
        self.request(
            "PATCH",
            f"/memory/place/{self.place_id}/visual-index",
            expected=422,
            json={"status": "indexed", "backend": "salad"},
        )
        self.request(
            "PATCH",
            f"/memory/place/{self.place_id}/visual-index",
            expected=409,
            json={"status": "pending", "image_sha256": "0" * 64},
        )
        self.request(
            "PATCH",
            "/memory/place/plc_does_not_exist/visual-index",
            expected=404,
            json={"status": "pending"},
        )
        self.request(
            "PATCH",
            f"/memory/place/{self.object_id}/visual-index",
            expected=404,
            json={"status": "pending"},
        )

    def test_08_patch_preserves_other_extra_keys(self) -> None:
        self.request(
            "PATCH",
            f"/memory/place/{self.place_id}/visual-index",
            json={"status": "pending"},
        )
        with sqlite3.connect(self.data_dir / "memory_hub.db") as conn:
            raw = conn.execute(
                "SELECT extra_json FROM memories WHERE id = ?",
                (self.place_id,),
            ).fetchone()[0]
        extra = json.loads(raw)
        self.assertEqual(
            "verify visual place index stage one",
            extra["task_description"],
        )
        self.assertIn("image_captured_at", extra)
        self.assertEqual("pending", extra["visual_index"]["status"])

    def test_09_legacy_records_get_safe_defaults(self) -> None:
        legacy = self.request(
            "POST",
            "/memory/place/upsert",
            json={
                "place_name": "legacy_place",
                "robot_id": "test_robot",
                "robot_type": "test",
                "place_pose": {"x": 4, "y": 5},
                "image": reference_image_b64(),
            },
        ).json()
        with sqlite3.connect(self.data_dir / "memory_hub.db") as conn:
            conn.execute(
                "UPDATE memories SET extra_json = ? WHERE id = ?",
                ('{"legacy_key":"kept"}', legacy["place_id"]),
            )
        detail = self.request("GET", f"/memory/place/{legacy['place_id']}").json()
        self.assertEqual("not_indexed", detail["visual_index"]["status"])
        self.assertEqual(legacy["image_sha256"], detail["image_sha256"])
        self.request(
            "PATCH",
            f"/memory/place/{legacy['place_id']}/visual-index",
            json={"status": "pending"},
        )
        with sqlite3.connect(self.data_dir / "memory_hub.db") as conn:
            extra = json.loads(
                conn.execute(
                    "SELECT extra_json FROM memories WHERE id = ?",
                    (legacy["place_id"],),
                ).fetchone()[0]
            )
        self.assertEqual("kept", extra["legacy_key"])

        with sqlite3.connect(self.data_dir / "memory_hub.db") as conn:
            conn.execute(
                "UPDATE memories SET extra_json = ? WHERE id = ?",
                ("not-json", legacy["place_id"]),
            )
        detail = self.request("GET", f"/memory/place/{legacy['place_id']}").json()
        self.assertEqual("not_indexed", detail["visual_index"]["status"])

    def test_10_query_place_is_compatibly_enhanced(self) -> None:
        result = self.request(
            "POST",
            "/query/place",
            json={"name": "vpr_stage1_test_place"},
        ).json()["results"][0]
        for field in ("id", "target_pose", "evidence", "place_id", "image_id", "image_sha256", "visual_index"):
            self.assertIn(field, result)
        self.assertEqual(self.upsert_result["image_path"], result["evidence"]["image_path"])

    def test_11_regression_health_object_position_and_unified_queries(self) -> None:
        self.assertEqual("ok", self.request("GET", "/health").json()["status"])
        object_results = self.request(
            "POST",
            "/query/object",
            json={"name": "vpr_stage1_test_object"},
        ).json()["results"]
        self.assertTrue(object_results)
        position_results = self.request(
            "POST",
            "/query/position",
            json={"x": 1.25, "y": -2.5, "radius": 0.1},
        ).json()["results"]
        self.assertTrue(position_results)
        unified_results = self.request(
            "POST",
            "/query/unified",
            json={"place_name": "vpr_stage1_test_place"},
        ).json()["results"]
        self.assertTrue(unified_results)

    def test_12_concurrent_patches_leave_valid_atomic_metadata(self) -> None:
        barrier = threading.Barrier(5)

        def patch(index: int) -> int:
            barrier.wait()
            return requests.patch(
                f"{self.base_url}/memory/place/{self.place_id}/visual-index",
                json={"status": "failed", "error": f"worker-{index}"},
                timeout=5,
            ).status_code

        with ThreadPoolExecutor(max_workers=5) as executor:
            statuses = list(executor.map(patch, range(5)))
        self.assertEqual([200] * 5, statuses)
        with sqlite3.connect(self.data_dir / "memory_hub.db") as conn:
            raw = conn.execute(
                "SELECT extra_json FROM memories WHERE id = ?",
                (self.place_id,),
            ).fetchone()[0]
        extra = json.loads(raw)
        self.assertEqual("verify visual place index stage one", extra["task_description"])
        self.assertEqual("failed", extra["visual_index"]["status"])


if __name__ == "__main__":
    unittest.main()
