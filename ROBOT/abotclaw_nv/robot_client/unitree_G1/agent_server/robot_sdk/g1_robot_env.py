"""G1 统一执行环境 — 对齐 Piper 的 ``PiperRobotEnv`` 思路。

在 ``/code/execute`` 子进程中通过 ``install_into_globals`` 注入：

- ``env``：本类实例（推荐 ``env.read_cameras()`` / ``env.yolo`` 等）
- 兼容别名：``camera``、``camera_d435i``、``yolo``、``memory``、``face``、``tts``、``vision``、``grasp_target``、``grasp_something``、``release_object``、``release_something``
- ``Nav2Anywhere``：ROS2 导航客户端类（``navigation_sdk``，需运行前已 ``source /opt/ros/humble/setup.bash``）

与 Piper 形状对齐的便捷方法：

- ``read_cameras()`` → ``(images, timestamps)``，键含 ``d455_rgb`` / ``d455_depth``，可选 ``d435i_*``
- ``get_robot_state()`` / ``get_robot_end_pose()``：G1 无 Piper 同款 ROS 流时返回占位 dict / ``None``（见文档串）
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Tuple


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
    def face(self) -> Any:
        return self._face

    @property
    def vision(self) -> Any:
        return self._vision

    @property
    def tts(self) -> Any:
        return self._tts

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
        g["release_object"] = release_object
        g["release_something"] = release_something
        g["camera"] = self._camera
        g["camera_d435i"] = self._camera_d435i
        g["yolo"] = self._yolo
        g["memory"] = self._memory
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
