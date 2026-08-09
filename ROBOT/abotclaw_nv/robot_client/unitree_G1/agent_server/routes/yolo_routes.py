"""YOLO visualization serving endpoint."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(prefix="/yolo", tags=["yolo"])

YOLO_VIZ_DIR = "/tmp/yolo_viz"


def _latest_home_d435i_yolo_jpg() -> str | None:
    """Most recent ``~/d435i_yolo_*.jpg`` from ``yolo.save_detection_image()`` (mtime)."""
    try:
        home = Path.home()
        candidates = sorted(
            home.glob("d435i_yolo_*.jpg"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
    except OSError:
        pass
    return None


@router.get("/visualization", include_in_schema=False)
async def get_visualization():
    """Return a recent YOLO-annotated JPEG.

    Resolution order:

    1. ``/tmp/yolo_viz/latest.jpg`` (legacy / other pipelines)
    2. Newest ``~/d435i_yolo_*.jpg`` from ``YoloSDK.save_detection_image()`` (same user as server)

    No lease required.
    """
    viz_path = os.path.join(YOLO_VIZ_DIR, "latest.jpg")
    if os.path.isfile(viz_path):
        return FileResponse(viz_path, media_type="image/jpeg")
    latest_home = _latest_home_d435i_yolo_jpg()
    if latest_home and os.path.isfile(latest_home):
        return FileResponse(latest_home, media_type="image/jpeg")
    return JSONResponse(
        {
            "error": (
                "No visualization available. Run yolo.save_detection_image() or write "
                "/tmp/yolo_viz/latest.jpg"
            )
        },
        status_code=404,
    )
