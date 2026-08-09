"""YOLO Object Detection API Service.

FastAPI backend that receives base64 encoded images and returns bounding boxes 
using a local YOLOv5 model.
"""

from __future__ import annotations

import base64
import io
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional

import requests
import torch
import yolov5
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

SERVICE_VERSION = "0.1.0"
BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(os.getenv("YOLO_MODEL_PATH", str(BASE_DIR / "yolov5l6.pt"))).resolve()


def _resolve_torch_device(env_value: Optional[str]) -> str:
    value = (env_value or "auto").strip().lower()
    if value == "auto":
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if value == "cpu":
        return "cpu"
    if value == "cuda":
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if value.isdigit():
        return f"cuda:{value}" if torch.cuda.is_available() else "cpu"
    if value.startswith("cuda:"):
        return value if torch.cuda.is_available() else "cpu"
    return value


DEVICE = _resolve_torch_device(os.getenv("DEVICE") or os.getenv("YOLO_DEVICE"))

YOLO_MODEL = None
INFER_LOCK = threading.Lock()


class DetectRequest(BaseModel):
    image: str = Field(..., description="Base64-encoded JPEG/PNG image")
    conf_thres: float = Field(0.25, description="Confidence threshold")
    iou_thres: float = Field(0.45, description="NMS IoU threshold")


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int
    class_name: str


class DetectResponse(BaseModel):
    detections: List[BoundingBox]
    count: int


def _decode_image(image_input: str) -> Image.Image:
    try:
        payload = image_input.strip()
        if payload.startswith(("http://", "https://")):
            response = requests.get(payload, timeout=15)
            response.raise_for_status()
            return Image.open(io.BytesIO(response.content)).convert("RGB")
        if payload.startswith("data:image"):
            payload = payload.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(payload, validate=True)
            return Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception:
            if Path(payload).exists():
                return Image.open(payload).convert("RGB")
            raise ValueError("Invalid base64 string and path not found")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image payload: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global YOLO_MODEL
    print(f"Loading YOLO model from {MODEL_PATH} on {DEVICE}...")
    try:
        YOLO_MODEL = yolov5.load(str(MODEL_PATH), device=DEVICE)
    except Exception as e:
        print(f"Failed to load YOLO model from {MODEL_PATH}: {e}")
        raise RuntimeError(f"YOLO model load failed: {e}") from e
    yield
    YOLO_MODEL = None


app = FastAPI(
    title="AbotClaw YOLO Detection Service",
    version=SERVICE_VERSION,
    description="Run YOLO object detection on incoming base64 images.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": SERVICE_VERSION,
        "device": DEVICE,
        "model_path": str(MODEL_PATH),
        "model_loaded": YOLO_MODEL is not None,
    }


@app.post("/detect", response_model=DetectResponse)
def detect(req: DetectRequest) -> DetectResponse:
    if YOLO_MODEL is None:
        raise HTTPException(status_code=503, detail="Model not initialized")

    image = _decode_image(req.image)

    with INFER_LOCK:
        YOLO_MODEL.conf = req.conf_thres
        YOLO_MODEL.iou = req.iou_thres
        results = YOLO_MODEL(image)

        # Parse results using yolov5's pandas interface
        df = results.pandas().xyxy[0]

    detections = []
    for _, row in df.iterrows():
        detections.append(
            BoundingBox(
                x1=float(row['xmin']),
                y1=float(row['ymin']),
                x2=float(row['xmax']),
                y2=float(row['ymax']),
                confidence=float(row['confidence']),
                class_id=int(row['class']),
                class_name=str(row['name'])
            )
        )

    return DetectResponse(detections=detections, count=len(detections))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8013"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
