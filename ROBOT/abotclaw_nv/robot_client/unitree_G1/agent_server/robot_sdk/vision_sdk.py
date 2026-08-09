"""
Vision SDK — 调用多模态 VL 模型描述 D455 当前画面。

模型、base_url、api_key 等全部从 config.yaml 的 ``vision`` 段读取，
不硬编码任何厂商 / 模型名。

config.yaml 示例::

    vision:
      base_url: "http://localhost:9988/v1"   # 或 https://api.openai.com/v1 等
      api_key: "sk-xxx"                      # 也可用环境变量 VISION_API_KEY
      model: "kimi-k2.6"                     # 任何兼容 OpenAI VL 的模型 ID
      timeout: 90.0
      max_side: 512                          # 图片缩放最大边长
      default_prompt: "请用中文详细描述这张图片中的场景。"

用法（在 /code/execute 里）::

    result = vision.describe_current_frame()
    print(result)

    result = vision.describe_current_frame(prompt="画面里有几个人？")

    rgb, _ = camera.get_frame()
    result = vision.describe(rgb, prompt="这是什么地方？")
"""

from __future__ import annotations

import base64
import os
from typing import Any, Optional

import cv2
import numpy as np

try:
    from .config import get_config
except ImportError:
    from config import get_config


class VisionSDK:
    """通用多模态 VL 客户端（OpenAI-compatible），仿照 FaceSDK 风格。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        camera: Any = None,
        timeout: Optional[float] = None,
    ):
        cfg = get_config()
        vcfg = cfg.get("vision", {})

        self._api_key = (
            api_key
            or vcfg.get("api_key")
            or os.environ.get("VISION_API_KEY")
            or self._load_openclaw_api_key()
        )
        if not self._api_key:
            raise ValueError(
                "VisionSDK: 需要 API key。"
                "在 config.yaml vision.api_key 或环境变量 VISION_API_KEY 中配置。"
            )

        self._base_url = base_url or vcfg.get("base_url", "")
        if not self._base_url:
            raise ValueError(
                "VisionSDK: 需要 base_url。在 config.yaml vision.base_url 中配置。"
            )

        self._model = model or vcfg.get("model", "")
        if not self._model:
            raise ValueError(
                "VisionSDK: 需要 model。在 config.yaml vision.model 中配置。"
            )

        self._timeout = timeout or vcfg.get("timeout", 90.0)
        self._max_side = vcfg.get("max_side", 512)
        self._default_prompt = vcfg.get(
            "default_prompt",
            "请用中文详细描述这张图片中的场景，包括人物、物体、环境和正在发生的事情。",
        )
        self._camera = camera

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------

    def describe_current_frame(
        self,
        prompt: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        """拍一帧 D455 画面，调 VL 模型返回描述文本。"""
        if self._camera is None:
            raise RuntimeError("VisionSDK: 没有可用的相机（camera 未注入）")

        rgb, _ = self._camera.get_frame()
        if rgb is None:
            raise RuntimeError("VisionSDK: D455 取帧失败（rgb 为 None）")

        return self.describe(rgb, prompt=prompt, max_tokens=max_tokens)

    def describe(
        self,
        image: np.ndarray,
        prompt: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        """对任意 numpy RGB 图像调 VL 模型，返回描述文本。"""
        import requests as _requests

        prompt = prompt or self._default_prompt
        img_b64 = self._encode_rgb(image, max_side=self._max_side)

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}"
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "stream": True,
        }

        url = f"{self._base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        resp = _requests.post(
            url, json=payload, headers=headers, timeout=self._timeout, stream=True
        )
        resp.raise_for_status()

        chunks: list[str] = []
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            line_str = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line_str.startswith("data: "):
                continue
            data_str = line_str[len("data: "):]
            if data_str.strip() == "[DONE]":
                break
            try:
                import json as _json
                chunk = _json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                text = delta.get("content", "")
                if text:
                    chunks.append(text)
            except Exception:
                continue

        return "".join(chunks).strip()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_rgb(rgb: np.ndarray, max_side: int = 512) -> str:
        """numpy RGB → 缩放 + JPEG base64。"""
        h, w = rgb.shape[:2]
        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise RuntimeError("VisionSDK: JPEG 编码失败")
        return base64.b64encode(buf.tobytes()).decode("ascii")

    @staticmethod
    def _load_openclaw_api_key() -> Optional[str]:
        """兜底：从 ~/.openclaw/openclaw.json 读第一个 provider 的 apiKey。"""
        import json

        path = os.path.expanduser("~/.openclaw/openclaw.json")
        try:
            with open(path) as f:
                data = json.load(f)
            providers = data.get("models", {}).get("providers", {})
            for _, pcfg in providers.items():
                key = pcfg.get("apiKey")
                if key:
                    return key
        except Exception:
            pass
        return None

    def close(self) -> None:
        pass

    def __enter__(self) -> "VisionSDK":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
