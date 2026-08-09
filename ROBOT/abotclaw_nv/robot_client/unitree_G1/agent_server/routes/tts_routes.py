"""TTS (Text-to-Speech) API routes for G1 robot."""

from __future__ import annotations

import os
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from robot_sdk.tts_sdk import TTSClient

router = APIRouter(prefix="/tts", tags=["G1 TTS"])


def _default_iface() -> str:
    return os.environ.get(
        "G1_ARM_NETWORK_IFACE",
        os.environ.get(
            "G1_ARM_NETWORK_INTERFACE",
            os.environ.get("UNITREE_IFACE", os.environ.get("G1_NETWORK_INTERFACE", "enp4s0")),
        ),
    )


# 单例：TTSClient 内部依赖全局 DDS ChannelFactory，重复 initialize() 无意义且可能失败
_client_lock = threading.Lock()
_client: TTSClient | None = None


def _get_client() -> TTSClient:
    global _client
    with _client_lock:
        if _client is None:
            client = TTSClient(iface=_default_iface())
            if not client.initialize():
                raise HTTPException(status_code=503, detail="Failed to initialize TTS client")
            _client = client
        return _client


class TTSSpeakRequest(BaseModel):
    text: str = Field(..., description="Text to speak (Chinese supported)")
    speaker_id: int = Field(default=0, description="Speaker ID")


class TTSVolumeRequest(BaseModel):
    volume: int = Field(..., ge=0, le=100, description="Volume level (0-100)")


class TTSResponse(BaseModel):
    success: bool
    message: str


class TTSVolumeResponse(BaseModel):
    success: bool
    volume: int | None = None
    message: str


@router.post("/speak", response_model=TTSResponse)
async def tts_speak_endpoint(body: TTSSpeakRequest) -> TTSResponse:
    """Speak text using G1 TTS."""
    client = _get_client()
    if client.speak(body.text, body.speaker_id):
        return TTSResponse(success=True, message="TTS spoken successfully")
    return TTSResponse(success=False, message="TTS failed")


@router.get("/volume", response_model=TTSVolumeResponse)
async def tts_get_volume() -> TTSVolumeResponse:
    """Get current TTS volume."""
    client = _get_client()
    volume = client.get_volume()
    if volume is not None:
        return TTSVolumeResponse(success=True, volume=volume, message="Volume retrieved")
    return TTSVolumeResponse(success=False, volume=None, message="Failed to get volume")


@router.post("/volume", response_model=TTSResponse)
async def tts_set_volume(body: TTSVolumeRequest) -> TTSResponse:
    """Set TTS volume."""
    client = _get_client()
    if client.set_volume(body.volume):
        return TTSResponse(success=True, message=f"Volume set to {body.volume}")
    return TTSResponse(success=False, message="Failed to set volume")
