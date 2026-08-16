"""G1 统一执行环境 — 对齐 Piper 的 ``PiperRobotEnv`` 思路。

在 ``/code/execute`` 子进程中通过 ``install_into_globals`` 注入：

- ``env``：本类实例（推荐 ``env.read_cameras()`` / ``env.yolo`` 等）
- 兼容别名：``camera``、``camera_d435i``、``yolo``、``memory``、``face``、``tts``、``vision``、``vlac``、``grasp_target``、``grasp_something``、``grasp_with_vlac``、``release_object``、``release_something``
- ``Nav2Anywhere``：ROS2 导航客户端类（``navigation_sdk``，需运行前已 ``source /opt/ros/humble/setup.bash``）

与 Piper 形状对齐的便捷方法：

- ``read_cameras()`` → ``(images, timestamps)``，键含 ``d455_rgb`` / ``d455_depth``，可选 ``d435i_*``
- ``get_robot_state()`` / ``get_robot_end_pose()``：G1 无 Piper 同款 ROS 流时返回占位 dict / ``None``（见文档串）
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import cv2

from .vpr_sdk import VPRSDK

class _D435iFrameView:
    __slots__ = ("rgb", "depth")

    def __init__(self, rgb: Any, depth: Any) -> None:
        self.rgb = rgb
        self.depth = depth

    def __iter__(self) -> Any:
        yield self.rgb
        yield self.depth

    def __bool__(self) -> bool:
        return self.rgb is not None

    @property
    def shape(self) -> Any:
        return self.rgb.shape

    @property
    def dtype(self) -> Any:
        return self.rgb.dtype


class _LazyCameraD435i:
    def __init__(self, getter: Callable[[], Any]) -> None:
        self._getter = getter

    def __call__(self) -> Any:
        return self._getter()

    def get_frame(self) -> Any:
        cam = self._getter()
        if cam is None:
            return None
        rgb, depth = cam.get_frame()
        if rgb is None:
            return None
        return _D435iFrameView(rgb, depth)

    def __getattr__(self, name: str) -> Any:
        cam = self._getter()
        if cam is None:
            raise RuntimeError("D435i camera not initialized or unavailable")
        return getattr(cam, name)


class _LazyYolo:
    def __init__(self, get_yolo: Callable[[], Any]) -> None:
        self._get_yolo = get_yolo
        self._yolo: Any = None

    def _get(self) -> Any:
        if self._yolo is None:
            self._yolo = self._get_yolo()
        return self._yolo

    def detect_on_rgb(self, *args: Any, **kwargs: Any) -> Any:
        return self._get().detect_on_rgb(*args, **kwargs)

    def detect_env(self) -> Any:
        return self._get().detect_env()

    def segment_3d(self, object_name: Any) -> Any:
        return self._get().segment_3d(object_name)

    def segment_camera(self, camera_name: Any, label: Any = None) -> Any:
        return self._get().segment_camera(camera_name, label)

    def save_detection_image(self, *args: Any, **kwargs: Any) -> Any:
        return self._get().save_detection_image(*args, **kwargs)

    def start(self) -> Any:
        return self._get().start()

    def stop(self) -> None:
        if self._yolo is not None:
            self._yolo.stop()


class _LazyFace:
    def __init__(self, get_face: Callable[[], Any]) -> None:
        self._get_face = get_face
        self._face: Any = None

    def _get(self) -> Any:
        if self._face is None:
            self._face = self._get_face()
        return self._face

    def start(self) -> Any:
        return self._get().start()

    def health(self) -> Any:
        return self._get().health()

    def list_people(self) -> Any:
        return self._get().list_people()

    def enroll(self, *args: Any, **kwargs: Any) -> Any:
        return self._get().enroll(*args, **kwargs)

    def batch_enroll(self, *args: Any, **kwargs: Any) -> Any:
        return self._get().batch_enroll(*args, **kwargs)

    def recognize(self, *args: Any, **kwargs: Any) -> Any:
        return self._get().recognize(*args, **kwargs)

    def recognize_current_frame(self, *args: Any, **kwargs: Any) -> Any:
        return self._get().recognize_current_frame(*args, **kwargs)

    def stop(self) -> None:
        if self._face is not None:
            self._face.stop()


class G1RobotEnv:
    """G1 ``/code/execute`` 聚合入口：相机、感知、记忆、人脸、TTS 与 ``grasp_target``。"""

    def __init__(
        self,
        robot_ip: str,
        d435i_host: str,
        d435i_port: int,
    ) -> None:
        from robot_sdk.g1_grasp_sdk import grasp_target
        try:
            from .g1_d455_camera import G1D455Camera
            from .g1_d435i_camera import G1D435iCamera
            from .yolo_sdk import YoloSDK
            from .memory_sdk import MemorySDK
            from .face_sdk import FaceSDK
            from .tts_sdk import TTSClient
        except ImportError:
            from g1_d455_camera import G1D455Camera
            from g1_d435i_camera import G1D435iCamera
            from yolo_sdk import YoloSDK
            from memory_sdk import MemorySDK
            from face_sdk import FaceSDK
            from tts_sdk import TTSClient

        self._grasp_target = grasp_target

        self._camera = G1D455Camera(host=robot_ip, enable_depth=True)
        self._camera.initialize()

        self._cam_d435i = G1D435iCamera(host=d435i_host, port=d435i_port, enable_depth=True)
        self._d435i_initialized = False

        def _ensure_d435i() -> Any:
            if not self._d435i_initialized:
                self._d435i_initialized = self._cam_d435i.initialize()
                if not self._d435i_initialized:
                    print("[wrapper] D435i camera not available (service may be offline)")
            return self._cam_d435i if self._d435i_initialized else None

        self._camera_d435i = _LazyCameraD435i(_ensure_d435i)

        self._yolo_holder: Dict[str, Any] = {"v": None}

        def _get_yolo() -> Any:
            if self._yolo_holder["v"] is None:
                self._yolo_holder["v"] = YoloSDK(camera=self._cam_d435i)
                self._yolo_holder["v"].start()
                if getattr(self._cam_d435i, "_initialized", False):
                    self._d435i_initialized = True
            return self._yolo_holder["v"]

        self._yolo = _LazyYolo(_get_yolo)
        self._memory = MemorySDK()
        self._vpr = VPRSDK()
        try:
            from .vlac_sdk import VLACSDK
        except ImportError:
            from vlac_sdk import VLACSDK
        self._vlac = VLACSDK()

        self._face_holder: Dict[str, Any] = {"v": None}

        def _get_face() -> Any:
            if self._face_holder["v"] is None:
                self._face_holder["v"] = FaceSDK()
                self._face_holder["v"].start()
            return self._face_holder["v"]

        self._face = _LazyFace(_get_face)
        self._tts = TTSClient()

        # Vision（Kimi VL 多模态描述）—— 懒加载，首次访问时初始化
        self._vision_holder: Dict[str, Any] = {"v": None}

        def _get_vision() -> Any:
            if self._vision_holder["v"] is None:
                from .vision_sdk import VisionSDK
                self._vision_holder["v"] = VisionSDK(camera=self._camera)
            return self._vision_holder["v"]

        class _LazyVision:
            """代理：首次调方法时才构造 VisionSDK。"""
            __slots__ = ("_factory",)
            def __init__(self, factory: Callable[[], Any]) -> None:
                self._factory = factory
            def __getattr__(self, name: str) -> Any:
                return getattr(self._factory(), name)

        self._vision = _LazyVision(_get_vision)

    @property
    def grasp_target(self) -> Any:
        return self._grasp_target

    @property
    def camera(self) -> Any:
        return self._camera

    @property
    def camera_d435i(self) -> Any:
        return self._camera_d435i

    @property
    def yolo(self) -> Any:
        return self._yolo

    @property
    def memory(self) -> Any:
        return self._memory
    @property
    def vpr(self):
        return self._vpr

    @property
    def vlac(self) -> Any:
        return self._vlac

    @property
    def face(self) -> Any:
        return self._face

    @property
    def vision(self) -> Any:
        return self._vision

    @property
    def tts(self) -> Any:
        return self._tts

    def grasp_with_vlac(
        self,
        object_name: str,
        *,
        task_description: str | None = None,
        settle_seconds: float = 2.0,
        **grasp_kwargs: Any,
    ) -> Dict[str, Any]:
        """Execute the stable grasp, capturing After at lift_return for VLAC.

        VLAC failures are reported in the returned structure and never replace
        the underlying grasp execution result. ``settle_seconds`` is retained
        for call compatibility but no longer controls or delays After capture.
        """
        from robot_sdk.grasp_something_sdk import grasp_something

        description = task_description or f"Pick up the {object_name} from the table."
        result: Dict[str, Any] = {
            "execution_success": False,
            "critic_available": False,
            "verification_available": False,
            "critic_score": None,
            "critic_list": None,
            "value_list": None,
            "grasp_decision": None,
            "removal_confirmed": None,
            "holding_score": None,
            "holding_confirmed": None,
            "reward": None,
            "done": None,
        }
        errors = []
        capture_timestamp = int(time.time())
        vlac_log_dir = Path(__file__).resolve().parent.parent / "logs"

        before_rgb = None
        print("[VLAC] Capturing Before RGB")
        try:
            before_frame = self._camera_d435i.get_frame()
            before_rgb = getattr(before_frame, "rgb", None) if before_frame else None
            if before_rgb is None:
                raise RuntimeError("D435i Before RGB unavailable")
            before_rgb = before_rgb.copy()
            try:
                vlac_log_dir.mkdir(parents=True, exist_ok=True)
                before_path = vlac_log_dir / f"vlac_before_{capture_timestamp}.jpg"
                before_bgr = cv2.cvtColor(before_rgb, cv2.COLOR_RGB2BGR)
                if not cv2.imwrite(str(before_path), before_bgr):
                    raise RuntimeError("cv2.imwrite returned False")
                print(f"[VLAC] Before RGB saved: {before_path}")
            except Exception as exc:
                print(f"[VLAC] Warning: failed to save Before RGB: {exc}")
        except Exception as exc:
            errors.append(f"before capture: {exc}")

        after_holder: Dict[str, Any] = {"rgb": None}

        def _capture_after_lift() -> None:
            print("[VLAC] Capturing After RGB after lift_return")
            try:
                frame = self._camera_d435i.get_frame()
                rgb = getattr(frame, "rgb", None) if frame else None
                if rgb is None:
                    raise RuntimeError("D435i After RGB unavailable")
                after_holder["rgb"] = rgb.copy()
                try:
                    vlac_log_dir.mkdir(parents=True, exist_ok=True)
                    after_path = (
                        vlac_log_dir
                        / f"vlac_after_step5_{capture_timestamp}.jpg"
                    )
                    after_bgr = cv2.cvtColor(
                        after_holder["rgb"], cv2.COLOR_RGB2BGR
                    )
                    if not cv2.imwrite(str(after_path), after_bgr):
                        raise RuntimeError("cv2.imwrite returned False")
                    print(f"[VLAC] After RGB saved: {after_path}")
                except Exception as exc:
                    print(f"[VLAC] Warning: failed to save After RGB: {exc}")
                print("[VLAC] After RGB captured successfully")
            except Exception as exc:
                # Camera/VLAC failures must not prevent the final home motion.
                errors.append(f"after capture: {exc}")

        try:
            result["execution_success"] = bool(
                grasp_something(
                    object_name,
                    after_lift_callback=_capture_after_lift,
                    **grasp_kwargs,
                )
            )
        except Exception as exc:
            result["execution_error"] = str(exc)

        # Kept only for backward-compatible calls; After is already fixed above.
        _ = settle_seconds
        after_rgb = after_holder["rgb"]

        if before_rgb is not None and after_rgb is not None:
            try:
                critic = self._vlac.evaluate_progress(
                    before_image=before_rgb,
                    after_image=after_rgb,
                    task_description=description,
                )
                critic_score = float(critic["critic_list"][0])
                result.update(
                    critic_available=True,
                    critic_score=critic_score,
                    critic_list=critic["critic_list"],
                    value_list=critic.get("value_list"),
                    critic_latency_ms=critic.get("latency_ms"),
                    reward=critic_score,
                    critic_result=critic,
                )
            except Exception as exc:
                errors.append(f"critic: {exc}")

            try:
                holding = self._vlac.verify_holding(
                    after_image=after_rgb,
                    target_label=object_name,
                )
                holding_score = float(holding["holding_score"])
                done = bool(holding["grasp_success"])
                result.update(
                    verification_available=True,
                    holding_score=holding_score,
                    holding_confirmed=done,
                    done=done,
                    holding_result=holding,
                    grasp_verification=holding,
                )
            except Exception as exc:
                errors.append(f"grasp holding verification: {exc}")

        if errors:
            result["vlac_error"] = "; ".join(errors)
        return result

    def read_cameras(self) -> Tuple[Dict[str, Any], Dict[str, float]]:
        """对齐 ``PiperRobotEnv.read_cameras``：返回 ``(images, timestamps)``。

        ``images`` 至少含 ``d455_rgb``（及 ``d455_depth`` 若可用）；若 D435i 已连上则尝试加入
        ``d435i_rgb`` / ``d435i_depth``。未连上的相机不会出现在 dict 中。
        """
        images: Dict[str, Any] = {}
        timestamps: Dict[str, float] = {}
        t0 = time.time()
        rgb, depth = self._camera.get_frame()
        if rgb is not None:
            images["d455_rgb"] = rgb
            timestamps["d455_rgb"] = t0
        if depth is not None:
            images["d455_depth"] = depth
            timestamps["d455_depth"] = t0

        try:
            frame = self._camera_d435i.get_frame()
            if frame is not None:
                frgb = getattr(frame, "rgb", None)
                fdep = getattr(frame, "depth", None)
                t1 = time.time()
                if frgb is not None:
                    images["d435i_rgb"] = frgb
                    timestamps["d435i_rgb"] = t1
                if fdep is not None:
                    images["d435i_depth"] = fdep
                    timestamps["d435i_depth"] = t1
        except Exception:
            pass

        return images, timestamps

    def get_robot_state(self) -> Dict[str, Any]:
        """与 Piper 返回 dict 的形状预留对齐；当前未接关节话题，仅返回说明占位。"""
        return {
            "available": False,
            "note": (
                "G1 此环境未提供 Piper 同款 joint_positions 数组；"
                "关节/手臂状态请用 Agent Server 的 HTTP ``/state`` 或自研 DDS 订阅。"
            ),
        }

    def get_robot_end_pose(self) -> Any:
        """Piper 为末端位姿 dict；G1 未在此聚合，返回 ``None``。"""
        return None

    def install_into_globals(self, g: Dict[str, Any]) -> None:
        """写入 ``g``：``env`` + 与历史脚本兼容的顶层名。"""
        from robot_sdk import Nav2Anywhere
        from robot_sdk.g1_grasp_sdk import release_object
        from robot_sdk.grasp_something_sdk import grasp_something
        from robot_sdk.release_something_sdk import release_something
        from robot_sdk.memory_sdk import Pose

        g["env"] = self
        g["grasp_target"] = self._grasp_target
        g["grasp_something"] = grasp_something
        g["grasp_with_vlac"] = self.grasp_with_vlac
        g["release_object"] = release_object
        g["release_something"] = release_something
        g["camera"] = self._camera
        g["camera_d435i"] = self._camera_d435i
        g["yolo"] = self._yolo
        g["memory"] = self._memory
        g["vpr"] = self._vpr
        g["vlac"] = self._vlac
        g["face"] = self._face
        g["tts"] = self._tts
        g["vision"] = self._vision
        g["Pose"] = Pose
        g["Nav2Anywhere"] = Nav2Anywhere

    def shutdown(self) -> None:
        """与 ``code_execute_bootstrap.shutdown_executor_runtime`` 相同顺序。"""
        try:
            self._face.stop()
        except Exception:
            pass
        try:
            self._yolo.stop()
        except Exception:
            pass
        try:
            self._camera.close()
        except Exception:
            pass
        if self._d435i_initialized and self._cam_d435i is not None:
            try:
                self._cam_d435i.close()
            except Exception:
                pass

    @classmethod
    def from_config(cls) -> "G1RobotEnv":
        """从 ``robot_sdk/config.yaml``（及 ``ROBOT_SDK_CONFIG``）构造。"""
        from .config import get_g1_d435i_host, get_g1_d435i_port, get_g1_robot_ip

        robot_ip = get_g1_robot_ip()
        d435i_host = get_g1_d435i_host(default=robot_ip)
        d435i_port = get_g1_d435i_port()
        return cls(robot_ip=robot_ip, d435i_host=d435i_host, d435i_port=d435i_port)


def grasp_with_vlac(
    env: G1RobotEnv,
    object_name: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Importable helper; ``/code/execute`` injects the bound env method."""
    return env.grasp_with_vlac(object_name, **kwargs)
