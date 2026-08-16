from __future__ import annotations

import base64
import unittest
from unittest import mock

import cv2
import numpy as np

from robot_sdk.g1_robot_env import G1RobotEnv
from robot_sdk.vlac_sdk import VLACError, VLACSDK


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def _decode_jpeg(value: str) -> np.ndarray:
    raw = base64.b64decode(value)
    return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)


class VLACSDKTest(unittest.TestCase):
    def test_critic_encodes_rgb_and_preserves_pair_order(self):
        before = np.zeros((8, 8, 3), dtype=np.uint8)
        before[:, :] = [255, 0, 0]  # RGB red
        after = np.zeros((8, 8, 3), dtype=np.uint8)
        after[:, :] = [0, 255, 0]  # RGB green

        response = {
            "critic_list": [60.0],
            "value_list": [0.0, 60.0],
            "latency_ms": 1200,
        }
        with mock.patch(
            "robot_sdk.vlac_sdk.requests.request",
            return_value=_Response(response),
        ) as request:
            result = VLACSDK(
                base_url="http://pc:8014", request_timeout=120
            ).evaluate_progress(before, after, "Pick up the bottle.")

        self.assertEqual(result, response)
        _, url = request.call_args.args[:2]
        payload = request.call_args.kwargs["json"]
        self.assertEqual(url, "http://pc:8014/critic")
        self.assertEqual(payload["batch_num"], 1)
        self.assertFalse(payload["rich"])

        reference_bgr = _decode_jpeg(payload["reference_image"])
        after_bgr = _decode_jpeg(payload["image"])
        self.assertGreater(int(reference_bgr[0, 0, 2]), 240)  # red stayed red
        self.assertGreater(int(after_bgr[0, 0, 1]), 240)  # green stayed green

    def test_critic_rejects_empty_critic_list(self):
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        with mock.patch(
            "robot_sdk.vlac_sdk.requests.request",
            return_value=_Response({"critic_list": []}),
        ):
            with self.assertRaisesRegex(VLACError, "critic_list"):
                VLACSDK(base_url="http://pc:8014").evaluate_progress(
                    image, image, "Pick up the bottle."
                )

    def test_holding_uses_after_image_only(self):
        after = np.zeros((8, 8, 3), dtype=np.uint8)
        response = {
            "holding_score": 0.91,
            "holding_threshold": 0.5,
            "grasp_success": True,
        }
        with mock.patch(
            "robot_sdk.vlac_sdk.requests.request",
            return_value=_Response(response),
        ) as request:
            result = VLACSDK(base_url="http://pc:8014").verify_holding(
                after, "bottle"
            )

        self.assertEqual(result, response)
        _, url = request.call_args.args[:2]
        payload = request.call_args.kwargs["json"]
        self.assertEqual(url, "http://pc:8014/grasp/holding")
        self.assertEqual(set(payload), {"after_image", "target_label", "rich"})
        self.assertEqual(payload["target_label"], "bottle")
        self.assertFalse(payload["rich"])


class _Frame:
    def __init__(self, value: int):
        self.rgb = np.full((2, 2, 3), value, dtype=np.uint8)


class _Camera:
    def __init__(self):
        self.calls = 0

    def get_frame(self):
        self.calls += 1
        return _Frame(self.calls)


class _VLAC:
    def __init__(self, fail=False):
        self.fail = fail
        self.before = None
        self.after = None

    def evaluate_progress(self, before_image, after_image, task_description):
        self.before = before_image
        self.after = after_image
        if self.fail:
            raise VLACError("offline")
        return {
            "critic_list": [60.0],
            "value_list": [0.0, 60.0],
            "latency_ms": 20,
        }

    def verify_holding(self, after_image, target_label):
        if self.fail:
            raise VLACError("offline")
        return {
            "holding_score": 0.91,
            "holding_threshold": 0.5,
            "grasp_success": True,
        }


def _successful_grasp(object_name, *, after_lift_callback=None, **kwargs):
    if after_lift_callback is not None:
        after_lift_callback()
    return True


class GraspWithVLACTest(unittest.TestCase):
    @staticmethod
    def _env(vlac):
        env = G1RobotEnv.__new__(G1RobotEnv)
        env._camera_d435i = _Camera()
        env._vlac = vlac
        return env

    @mock.patch(
        "robot_sdk.grasp_something_sdk.grasp_something",
        side_effect=_successful_grasp,
    )
    def test_returns_separate_execution_reward_and_done(self, grasp):
        vlac = _VLAC()
        env = self._env(vlac)
        result = env.grasp_with_vlac("bottle", settle_seconds=0)

        grasp.assert_called_once()
        self.assertEqual(grasp.call_args.args, ("bottle",))
        self.assertTrue(callable(grasp.call_args.kwargs["after_lift_callback"]))
        self.assertEqual(env._camera_d435i.calls, 2)
        self.assertEqual(int(vlac.before[0, 0, 0]), 1)
        self.assertEqual(int(vlac.after[0, 0, 0]), 2)
        self.assertTrue(result["execution_success"])
        self.assertEqual(result["reward"], 60.0)
        self.assertTrue(result["done"])
        self.assertEqual(result["holding_score"], 0.91)
        self.assertTrue(result["holding_confirmed"])
        self.assertIs(result["holding_result"], result["grasp_verification"])

    @mock.patch(
        "robot_sdk.grasp_something_sdk.grasp_something",
        side_effect=_successful_grasp,
    )
    def test_vlac_failure_does_not_erase_execution_success(self, grasp):
        result = self._env(_VLAC(fail=True)).grasp_with_vlac(
            "bottle", settle_seconds=0
        )

        self.assertTrue(result["execution_success"])
        self.assertFalse(result["critic_available"])
        self.assertIsNone(result["reward"])
        self.assertIsNone(result["done"])
        self.assertIn("offline", result["vlac_error"])


if __name__ == "__main__":
    unittest.main()
