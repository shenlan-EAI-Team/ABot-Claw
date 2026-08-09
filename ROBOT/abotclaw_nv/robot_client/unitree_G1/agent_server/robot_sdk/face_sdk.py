"""
Face service client for G1.

The service itself is HTTP-based. This wrapper uses G1D455Camera (ZMQ)
to capture live frames from the D455 head camera for face recognition.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import requests

try:
    from .config import get_config
    from .config import get_g1_robot_ip
    from .g1_d455_camera import G1D455Camera
except ImportError:
    # Fallback for direct module import (code execution context)
    from config import get_config
    from config import get_g1_robot_ip
    from g1_d455_camera import G1D455Camera


class FaceSDK:
    """HTTP client for the face-recognition service.
    
    **使用示例**::
    
        from robot_sdk.face_sdk import FaceSDK
        
        # 初始化（会自动启动 D455 相机）
        face = FaceSDK()
        face.start()
        
        # 列出已录入的人脸
        people = face.list_people()
        print("已录入：", people)
        
        # 录入新人脸
        face.enroll("zhangsan", ["path/to/image1.jpg", "path/to/image2.jpg"])
        
        # 识别当前帧
        result = face.recognize_current_frame()
        for match in result.get('results', []):
            print(f"识别到：{match['name']} ({match['match_score']:.2%})")
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        camera_backend: Optional[G1D455Camera] = None,
        request_timeout: Optional[float] = None,
        threshold: Optional[float] = None,
        camera_host: Optional[str] = None,
    ):
        cfg = get_config()
        face_cfg = cfg.get("face", {})
        g1_cfg = cfg.get("g1") or {}

        self._base_url = (
            base_url
            or os.environ.get("FACE_SERVICE_URL")
            or face_cfg.get("url", "http://127.0.0.1:8016")
        ).rstrip("/")
        self._request_timeout = (
            request_timeout if request_timeout is not None else face_cfg.get("request_timeout", 10.0)
        )
        self._threshold = threshold if threshold is not None else face_cfg.get("threshold", 0.45)
        self._camera_host = (
            camera_host
            or face_cfg.get("camera_host")
            or g1_cfg.get("robot_ip")
            or get_g1_robot_ip()
        )
        self._camera = camera_backend
        self._owns_camera = camera_backend is None
        self._started = False

    # ================================================================== #
    #                       生命周期
    # ================================================================== #

    def start(self) -> "FaceSDK":
        """Start the camera backend if needed."""
        if self._started:
            return self
        if self._camera is None:
            self._camera = G1D455Camera(host=self._camera_host, enable_depth=False)
        if not self._camera._initialized:
            self._camera.initialize()
        self._started = True
        return self

    def stop(self) -> None:
        """Stop the owned camera backend."""
        if not self._started:
            return
        if self._owns_camera and self._camera is not None:
            self._camera.close()
        self._started = False

    def __enter__(self) -> "FaceSDK":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass

    def _read_color(self):
        if not self._started:
            self.start()
        rgb = self._camera.get_rgb()
        if rgb is None:
            raise RuntimeError("Failed to get frame from D455 camera")
        # Convert RGB to BGR for OpenCV encoding
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    @staticmethod
    def _encode_image(img_bgr: np.ndarray) -> str:
        ok, buf = cv2.imencode(".png", img_bgr)
        if not ok:
            raise RuntimeError("cv2.imencode 失败")
        return base64.b64encode(buf).decode("utf-8")

    def _get(self, path: str) -> Dict[str, Any]:
        resp = requests.get(f"{self._base_url}{path}", timeout=self._request_timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = requests.post(
            f"{self._base_url}{path}",
            json=payload,
            timeout=self._request_timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ================================================================== #
    #                       公开 API
    # ================================================================== #

    def health(self) -> Dict[str, Any]:
        """获取人脸识别服务健康状态。"""
        return self._get("/health")

    def list_people(self) -> List[str]:
        """读取当前人脸库中的人员列表。"""
        data = self._get("/face/people")
        return data.get("people", [])

    def enroll(self, name: str, images: List[str]) -> Dict[str, Any]:
        """
        录入单个人员。

        Args:
            name: 人员名称
            images: 图像列表，支持 base64 / 路径 / URL
        """
        return self._post("/face/enroll", {"name": name, "images": images})

    def batch_enroll(self, people: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        批量录入多个人员。

        Args:
            people: [{"name": "zhangsan", "images": [...]}, ...]
        """
        return self._post("/face/enroll/batch", {"people": people})

    def recognize(
        self,
        image: str,
        threshold: Optional[float] = None,
        include_annotated_image: bool = False,
    ) -> Dict[str, Any]:
        """
        对单张图片执行人脸识别。

        Args:
            image: base64 / 路径 / URL
            threshold: 余弦相似度阈值
            include_annotated_image: 是否返回标注图 base64
        """
        return self._post(
            "/face/recognize",
            {
                "image": image,
                "threshold": self._threshold if threshold is None else threshold,
                "include_annotated_image": include_annotated_image,
            },
        )

    def recognize_current_frame(
        self,
        threshold: Optional[float] = None,
        include_annotated_image: bool = False,
    ) -> Dict[str, Any]:
        img = self._read_color()
        img_b64 = self._encode_image(img)
        return self.recognize(
            image=img_b64,
            threshold=threshold,
            include_annotated_image=include_annotated_image,
        )


if __name__ == "__main__":
    face = FaceSDK()
    with face:
        print("Health:", face.health())
        print("People:", face.list_people())
        print("Recognize result:", face.recognize_current_frame())
