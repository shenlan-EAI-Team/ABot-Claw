from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import types
import unittest

import numpy as np


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/abotclaw-run-robot-task/scripts/run_plan.py"
)
SPEC = importlib.util.spec_from_file_location("run_plan_under_test", SCRIPT)
run_plan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(run_plan)


class _PoseStamped:
    pass


class _Face:
    def start(self):
        return self

    def recognize_current_frame(self):
        return {"results": [{"name": "alice", "match_score": 0.9}]}


class _TTS:
    def __init__(self):
        self.initialized = 0

    def initialize(self):
        self.initialized += 1
        return True

    def speak(self, text):
        return bool(text)


class _Camera:
    def get_frame(self):
        return np.zeros((4, 4, 3), dtype=np.uint8), None


class _Yolo:
    def detect_on_rgb(self, rgb):
        return [
            {"class_name": "bottle", "confidence": 0.8},
            {"class_name": "chair", "confidence": 0.7},
        ]


def _execute_generated(plan, **overrides):
    geometry = types.ModuleType("geometry_msgs")
    geometry_msg = types.ModuleType("geometry_msgs.msg")
    geometry_msg.PoseStamped = _PoseStamped

    old_geometry = sys.modules.get("geometry_msgs")
    old_geometry_msg = sys.modules.get("geometry_msgs.msg")
    sys.modules["geometry_msgs"] = geometry
    sys.modules["geometry_msgs.msg"] = geometry_msg
    try:
        scope = {
            "face": _Face(),
            "tts": _TTS(),
            "camera": _Camera(),
            "yolo": _Yolo(),
            "grasp_something": lambda name: True,
            "grasp_with_vlac": lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("VLAC path should not run")
            ),
            "release_object": lambda: True,
            "Nav2Anywhere": object,
            "vpr": object(),
            "memory": object(),
        }
        scope.update(overrides)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(run_plan.build_code(plan), scope)
    finally:
        if old_geometry is None:
            sys.modules.pop("geometry_msgs", None)
        else:
            sys.modules["geometry_msgs"] = old_geometry
        if old_geometry_msg is None:
            sys.modules.pop("geometry_msgs.msg", None)
        else:
            sys.modules["geometry_msgs.msg"] = old_geometry_msg

    result_lines = [
        line for line in output.getvalue().splitlines()
        if line.startswith("RESULT_JSON=")
    ]
    return json.loads(result_lines[-1].split("=", 1)[1]), scope


class RunPlanCompatibilityTest(unittest.TestCase):
    def test_every_supported_type_has_generated_execution_branch(self):
        code = run_plan.build_code(
            {"steps": [{"id": "pause", "type": "wait", "seconds": 0}]}
        )
        compile(code, "<generated-plan>", "exec")
        for step_type in run_plan.SUPPORTED:
            pattern = rf"(?:if|elif) typ==['\"]{re.escape(step_type)}['\"]"
            self.assertRegex(code, pattern, step_type)

    def test_legacy_capabilities_and_plain_grasp_execute(self):
        plain_calls = []
        plan = {
            "steps": [
                {"id": "face", "type": "face_wait", "target": "alice"},
                {"id": "say", "type": "speak", "text": "hello"},
                {"id": "detect", "type": "detect_object", "object": "bottle"},
                {"id": "pick", "type": "grasp", "object": "bottle"},
                {"id": "drop", "type": "release"},
                {"id": "pause", "type": "wait", "seconds": 0},
            ]
        }
        run_plan.validate(plan)
        result, scope = _execute_generated(
            plan,
            grasp_something=lambda name: plain_calls.append(name) or True,
        )

        stages = {item["id"]: item for item in result["stages"]}
        self.assertEqual(result["status"], "success")
        self.assertTrue(stages["face"]["recognized"])
        self.assertTrue(stages["say"]["spoken"])
        self.assertTrue(stages["detect"]["found"])
        self.assertFalse(stages["pick"]["vlac_enabled"])
        self.assertTrue(stages["drop"]["released"])
        self.assertEqual(plain_calls, ["bottle"])
        self.assertEqual(scope["tts"].initialized, 1)

    def test_vlac_decision_status_mapping(self):
        decisions = iter(("REMOVED", "UNCERTAIN", "STILL_PRESENT"))

        def grasp_with_vlac(*args, **kwargs):
            decision = next(decisions)
            return {
                "execution_success": True,
                "grasp_decision": decision,
                "reward": 50.0,
                "done": decision == "REMOVED",
            }

        plan = {
            "steps": [
                {"id": "removed", "type": "grasp", "object": "a", "use_vlac": True},
                {"id": "uncertain", "type": "grasp", "object": "b", "use_vlac": True},
                {"id": "present", "type": "grasp", "object": "c", "use_vlac": True},
            ]
        }
        result, _ = _execute_generated(
            plan,
            grasp_with_vlac=grasp_with_vlac,
        )
        stages = {item["id"]: item for item in result["stages"]}
        self.assertEqual(stages["removed"]["status"], "success")
        self.assertEqual(stages["uncertain"]["status"], "partial")
        self.assertEqual(stages["present"]["status"], "failed")

    def test_use_vlac_requires_explicit_boolean(self):
        with self.assertRaisesRegex(ValueError, "use_vlac"):
            run_plan.validate(
                {"steps": [{"id": "pick", "type": "grasp", "object": "x", "use_vlac": "yes"}]}
            )


if __name__ == "__main__":
    unittest.main()
