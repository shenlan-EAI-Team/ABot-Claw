"""
VPR SDK for G1:
Visual Place Recognition HTTP Client

功能:
    - 上传地点视觉索引
    - 根据图片检索 place_id

PC Service:
    VisualPlaceRecognition
    port: 8030
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests

try:
    from .config import get_config
except ImportError:
    from config import get_config


class VPRSDK:
    """
    Visual Place Recognition HTTP Client

    Example:

        vpr = VPRSDK()

        result = vpr.search(
            image_path="/tmp/current.jpg"
        )

        print(result["place_id"])
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ):
        cfg = get_config()
        vpr_cfg = cfg.get("vpr", {})

        self._base_url = (
            base_url
            or os.environ.get("VPR_URL")
            or vpr_cfg.get(
                "url",
                "http://127.0.0.1:8030"
            )
        ).rstrip("/")

        self._timeout = (
            request_timeout
            if request_timeout is not None
            else vpr_cfg.get(
                "request_timeout",
                10.0
            )
        )


    def health(self) -> Dict[str, Any]:
        """
        检查VPR服务状态
        """
        try:
            resp = requests.get(
                f"{self._base_url}/health",
                timeout=self._timeout,
            )
            resp.raise_for_status()

            return {
                "status": "ok",
                "base_url": self._base_url,
            }

        except Exception as exc:
            return {
                "status": "error",
                "base_url": self._base_url,
                "error": str(exc),
            }


    def upload_image(
        self,
        place_id: str,
        image_path: str,
        image_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        上传地点视觉索引

        Args:
            place_id:
                SpatialMemory生成的地点ID

            image_path:
                本地图片路径

            image_id:
                图片ID
        """

        if image_id is None:
            image_id = os.path.basename(image_path)


        with open(image_path, "rb") as f:

            files = {
                "image": f
            }

            data = {
                "place_id": place_id,
                "image_id": image_id,
            }

            resp = requests.post(
                f"{self._base_url}/visual-index/images/upload",
                data=data,
                files=files,
                timeout=self._timeout,
            )

        resp.raise_for_status()

        return resp.json()


    def search(
        self,
        image_path: str,
    ) -> Dict[str, Any]:
        """
        根据当前图片搜索地点

        Returns:
            {
              place_id,
              score
            }
        """

        with open(image_path, "rb") as f:

            files = {
                "image": f
            }

            resp = requests.post(
                f"{self._base_url}/visual-index/search",
                files=files,
                timeout=self._timeout,
            )

        resp.raise_for_status()

        return resp.json()


    def close(self) -> None:
        """
        HTTP客户端无需释放资源
        """
        pass


    def __enter__(self):
        return self


    def __exit__(self, *args):
        self.close()