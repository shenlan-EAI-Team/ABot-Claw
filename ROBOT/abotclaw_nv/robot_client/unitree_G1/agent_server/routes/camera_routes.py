"""Camera routes for G1 D455 and D435i.

所有 IP 均从环境变量读取（由 config.env 统一管理）：
    G1_ROBOT_IP  — 机器人本体 IP（D455/D435i 推流均在此 IP 上）
"""

from __future__ import annotations

import os
import base64
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cameras", tags=["cameras"])

# 与 HTTP / SDK 约定的相机 ID（与 list_cameras 一致）
REGISTERED_CAMERA_IDS: Tuple[str, ...] = ("d455", "d435i")

# Global camera instances (initialized on first request)
_d455_camera_instance: Optional[Any] = None
_d435i_camera_instance: Optional[Any] = None


def _d455_host() -> str:
    return os.environ.get("D455_HOST") or os.environ.get("G1_ROBOT_IP", "192.168.123.164")


def _d435i_host() -> str:
    return os.environ.get("D435I_HOST") or os.environ.get("G1_ROBOT_IP", "192.168.123.164")


def get_d455_camera():
    """Get or create D455 camera instance."""
    global _d455_camera_instance
    if _d455_camera_instance is None:
        try:
            from robot_sdk import G1D455Camera

            _d455_camera_instance = G1D455Camera(
                host=_d455_host(),
                rgb_port=5555,
                depth_port=5556,
                width=640,
                height=480,
                timeout_ms=5000,
                enable_depth=True
            )
            
            if not _d455_camera_instance.initialize():
                logger.warning("Failed to initialize G1 D455 camera (robot may be offline)")
                _d455_camera_instance = None
                return None
                
            logger.info("G1 D455 camera initialized successfully")
            
        except Exception as e:
            logger.warning(f"D455 camera initialization skipped: {e}")
            _d455_camera_instance = None
            return None
    
    return _d455_camera_instance


def get_d435i_camera():
    """返回进程内单例 ``G1D435iCamera``（懒连接：此处不调用 ``initialize()``）。

    若在 ``GET /cameras`` 等路径上立即 ``connect``，会与 ``/code/execute`` 子进程里
    的取流 **并发占第二路 TCP**，推流端易出现多客户端、断连后 ``Broken pipe``。
    首次 ``get_frame()`` / ``initialize()`` 时再连 8765。
    """
    global _d435i_camera_instance
    if _d435i_camera_instance is None:
        try:
            from robot_sdk import G1D435iCamera

            _d435i_camera_instance = G1D435iCamera(
                host=_d435i_host(),
                port=8765,
                enable_depth=True,
            )
            logger.info("G1 D435i camera object created (TCP connects on first get_frame)")
        except Exception as e:
            logger.warning(f"D435i camera unavailable: {e}")
            _d435i_camera_instance = None
            return None

    return _d435i_camera_instance


@router.get("")
async def list_cameras():
    """List available cameras.
    
    Returns the G1 D455 and D435i RGB-D camera information.
    """
    cameras = []
    
    # D455 camera info
    d455_camera = get_d455_camera()
    d455_width, d455_height = 640, 480
    if d455_camera is not None:
        try:
            intrinsics = d455_camera.get_intrinsics()
            d455_width = intrinsics.get("width", 640)
            d455_height = intrinsics.get("height", 480)
        except Exception:
            pass
    
    cameras.append({
        "id": "d455",
        "name": "G1 D455 RGB-D Camera",
        "type": "rgbd",
        "transport": "zmq",
        "available": d455_camera is not None,
        "rgb_port": 5555,
        "depth_port": 5556,
        "width": d455_width,
        "height": d455_height,
        "has_depth": True,
    })
    
    # D435i：懒连接，listing 不抢 TCP；有 SDK 对象即视为可用（真实连通性见首次取流）
    d435i_camera = get_d435i_camera()
    d435i_width, d435i_height = 640, 480
    if d435i_camera is not None:
        try:
            intrinsics = d435i_camera.get_intrinsics()
            d435i_width = intrinsics.get("width", 640)
            d435i_height = intrinsics.get("height", 480)
        except Exception:
            pass

    cameras.append({
        "id": "d435i",
        "name": "G1 D435i RGB-D Camera",
        "type": "rgbd",
        "transport": "tcp",
        "available": d435i_camera is not None,
        "stream_port": 8765,
        "width": d435i_width,
        "height": d435i_height,
        "has_depth": True,
    })
    
    return {"cameras": cameras, "registered_ids": list(REGISTERED_CAMERA_IDS)}


def _get_camera_by_id(camera_id: str):
    """Get camera instance by ID."""
    if camera_id == "d455":
        return get_d455_camera()
    elif camera_id == "d435i":
        return get_d435i_camera()
    return None


# NOTE: Frame capture should be done through /code/execute endpoint
# This internal function is used by code executor, not exposed as HTTP endpoint
def _capture_frame_internal(
    camera_id: str,
    format: str = "jpeg",
    include_depth: bool = False,
    return_type: str = "image"
):
    """Internal frame capture function for code executor use only.
    
    Args:
        camera_id: Camera ID ("d455" or "d435i")
        format: Image format (jpeg or png)
        include_depth: Whether to include depth image
        return_type: "image" or "json"
    
    Returns:
        Raw bytes or dict with base64 data
    """
    if camera_id not in REGISTERED_CAMERA_IDS:
        raise ValueError(f"Camera '{camera_id}' not found")
    
    if not CV2_AVAILABLE:
        raise RuntimeError("OpenCV not available")
    
    camera = _get_camera_by_id(camera_id)
    if camera is None:
        raise RuntimeError(f"Camera '{camera_id}' not initialized")
    
    rgb, depth = camera.get_frame()
    
    if rgb is None:
        raise RuntimeError("Failed to capture frame")
    
    timestamp = time.time()
    
    # Encode RGB image
    if format.lower() == "png":
        encode_params = [cv2.IMWRITE_PNG_COMPRESSION, 6]
        ext = ".png"
        mime_type = "image/png"
    else:
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, 85]
        ext = ".jpg"
        mime_type = "image/jpeg"
    
    # Convert RGB to BGR for OpenCV encoding
    rgb_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    success, encoded = cv2.imencode(ext, rgb_bgr, encode_params)
    
    if not success:
        raise RuntimeError("Failed to encode image")
    
    if return_type == "image":
        return encoded.tobytes(), mime_type, timestamp
    else:
        # Return dict with base64 data
        rgb_base64 = base64.b64encode(encoded.tobytes()).decode("utf-8")
        
        response_data = {
            "camera_id": camera_id,
            "timestamp": timestamp,
            "rgb_base64": f"data:{mime_type};base64,{rgb_base64}"
        }
        
        if include_depth and depth is not None and NUMPY_AVAILABLE:
            if camera_id == "d455":
                # D455 depth is float32 in meters
                depth_mm = (depth * 1000).astype(np.uint16)
                success, depth_encoded = cv2.imencode(".png", depth_mm)
                depth_mime = "image/png"
            elif depth.ndim == 2:
                # D435i Z16 (uint16) — same encoding as mm PNG for inspection
                success, depth_encoded = cv2.imencode(".png", depth)
                depth_mime = "image/png"
            else:
                # D435i legacy: color-mapped H.264 decoded to RGB uint8
                depth_bgr = cv2.cvtColor(depth, cv2.COLOR_RGB2BGR)
                success, depth_encoded = cv2.imencode(".jpg", depth_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                depth_mime = "image/jpeg"

            if success:
                depth_base64 = base64.b64encode(depth_encoded.tobytes()).decode("utf-8")
                response_data["depth_base64"] = f"data:{depth_mime};base64,{depth_base64}"
        
        return response_data


@router.get("/{camera_id}/intrinsics")
async def get_intrinsics(camera_id: str):
    """Get camera intrinsics (calibration parameters).
    
    Args:
        camera_id: Camera ID (use "d455" or "d435i" for G1 RGB-D cameras)
    
    Returns:
        Camera intrinsics including fx, fy, cx, cy, width, height
    """
    if camera_id not in REGISTERED_CAMERA_IDS:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")
    
    camera = _get_camera_by_id(camera_id)
    if camera is None:
        # Return default intrinsics
        return {
            "camera_id": camera_id,
            "fx": 386.0,
            "fy": 386.0,
            "cx": 320.0,
            "cy": 240.0,
            "width": 640,
            "height": 480,
            "distortion": []
        }
    
    intrinsics = camera.get_intrinsics()
    return {
        "camera_id": camera_id,
        "fx": intrinsics.get("fx", 386.0),
        "fy": intrinsics.get("fy", 386.0),
        "cx": intrinsics.get("cx", 320.0),
        "cy": intrinsics.get("cy", 240.0),
        "width": intrinsics.get("width", 640),
        "height": intrinsics.get("height", 480),
        "distortion": []
    }


# NOTE: Video streaming should be done through /code/execute endpoint
# This internal generator is used by code executor, not exposed as HTTP endpoint
def _stream_frames_internal(camera_id: str):
    """Internal frame generator for code executor use only.
    
    Args:
        camera_id: Camera ID ("d455" or "d435i")
    
    Yields:
        JPEG encoded frame bytes
    """
    if camera_id not in REGISTERED_CAMERA_IDS:
        raise ValueError(f"Camera '{camera_id}' not found")
    
    if not CV2_AVAILABLE:
        raise RuntimeError("OpenCV not available")
    
    camera = _get_camera_by_id(camera_id)
    if camera is None:
        raise RuntimeError(f"Camera '{camera_id}' not initialized")
    
    while True:
        try:
            rgb, _ = camera.get_frame()
            if rgb is not None:
                # Convert RGB to BGR for JPEG encoding
                rgb_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                success, encoded = cv2.imencode(".jpg", rgb_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if success:
                    yield encoded.tobytes()
        except Exception as e:
            logger.error(f"Stream error: {e}")
            break


def cleanup_camera():
    """Cleanup camera resources."""
    global _d455_camera_instance, _d435i_camera_instance
    
    if _d455_camera_instance is not None:
        try:
            _d455_camera_instance.close()
            logger.info("D455 camera cleaned up")
        except Exception as e:
            logger.error(f"Error cleaning up D455 camera: {e}")
        finally:
            _d455_camera_instance = None
    
    if _d435i_camera_instance is not None:
        try:
            _d435i_camera_instance.close()
            logger.info("D435i camera cleaned up")
        except Exception as e:
            logger.error(f"Error cleaning up D435i camera: {e}")
        finally:
            _d435i_camera_instance = None
