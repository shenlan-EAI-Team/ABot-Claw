"""Routes module."""

from .camera_routes import (
    REGISTERED_CAMERA_IDS,
    router as camera_router,
    cleanup_camera,
    get_d455_camera,
    get_d435i_camera,
    _capture_frame_internal,
    _stream_frames_internal,
)
from .code_routes import router as code_router
from .display_routes import router as display_router
from .grasp_routes import router as grasp_router
from .lease_routes import router as lease_router
from .rewind_routes import router as rewind_router
from .sdk_docs import router as sdk_docs_router
from .service_routes import router as service_router
from .state_routes import router as state_router
from .system_guide import router as system_guide_router
from .workspace_routes import router as workspace_router
from .ws import router as ws_router
from .yolo_routes import router as yolo_router

__all__ = [
    "camera_router",
    "code_router",
    "display_router",
    "grasp_router",
    "lease_router",
    "rewind_router",
    "sdk_docs_router",
    "service_router",
    "state_router",
    "system_guide_router",
    "workspace_router",
    "ws_router",
    "yolo_router",
    "REGISTERED_CAMERA_IDS",
    "cleanup_camera",
    "get_d455_camera",
    "get_d435i_camera",
    "_capture_frame_internal",
    "_stream_frames_internal",
]
