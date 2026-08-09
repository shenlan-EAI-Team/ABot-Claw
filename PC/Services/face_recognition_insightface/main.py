"""InsightFace face recognition HTTP service."""

from __future__ import annotations

import base64
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import onnxruntime as ort
import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from insightface.app import FaceAnalysis
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from face_db import load_database, match_embedding, normalize_embedding, save_database

SERVICE_VERSION = "0.1.0"
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "face_db.json"
DEFAULT_PORT = 8016
DEFAULT_MODEL_NAME = os.getenv("FACE_RECOGNITION_MODEL", "buffalo_l")
DEFAULT_DET_SIZE = int(os.getenv("FACE_RECOGNITION_DET_SIZE", "640"))
DB_PATH = Path(os.getenv("FACE_DB_PATH", str(DEFAULT_DB_PATH))).resolve()
_CJK_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

FACE_APP: Optional[FaceAnalysis] = None
INFER_LOCK = threading.Lock()
DB_LOCK = threading.Lock()


class EnrollRequest(BaseModel):
    name: str = Field(..., description="Person name to enroll")
    images: List[str] = Field(..., min_length=1, description="Image payloads: base64/data-uri/path/url")


class BatchEnrollPerson(BaseModel):
    name: str = Field(..., description="Person name to enroll")
    images: List[str] = Field(..., min_length=1, description="Image payloads: base64/data-uri/path/url")


class BatchEnrollRequest(BaseModel):
    people: List[BatchEnrollPerson] = Field(..., min_length=1, description="Multiple people to enroll")


class EnrollResponse(BaseModel):
    name: str
    samples_received: int
    samples_used: int
    total_people: int
    db_path: str


class BatchEnrollItemResult(BaseModel):
    name: str
    samples_received: int
    samples_used: int


class BatchEnrollResponse(BaseModel):
    total_people: int
    db_path: str
    results: List[BatchEnrollItemResult]


class PeopleListResponse(BaseModel):
    count: int
    people: List[str]


class RecognizeRequest(BaseModel):
    image: str = Field(..., description="Image payload: base64/data-uri/path/url")
    threshold: float = Field(0.38, ge=-1.0, le=1.0, description="Cosine similarity threshold")
    include_annotated_image: bool = Field(False, description="Whether to return annotated image as base64 JPEG")


class FaceMatchResult(BaseModel):
    bbox: List[int]
    name: str
    match_score: float
    det_score: float


class RecognizeResponse(BaseModel):
    count: int
    threshold: float
    results: List[FaceMatchResult]
    latency_ms: float
    annotated_image: Optional[str] = None


def _resolve_ctx_id() -> int:
    value = (os.getenv("DEVICE") or os.getenv("FACE_RECOGNITION_CTX_ID") or "auto").strip().lower()
    providers = set(ort.get_available_providers())

    if value == "auto":
        return 0 if "CUDAExecutionProvider" in providers else -1
    if value in {"cpu", "-1"}:
        return -1
    if value == "cuda":
        return 0 if "CUDAExecutionProvider" in providers else -1
    try:
        return int(value)
    except ValueError:
        return -1


CTX_ID = _resolve_ctx_id()


def _put_text(image_bgr: np.ndarray, text: str, org: tuple[int, int], color: tuple[int, int, int]) -> np.ndarray:
    pil_img = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    try:
        font = ImageFont.truetype(_CJK_FONT_PATH, 22)
    except Exception:
        font = ImageFont.load_default()
    draw.text(org, text, font=font, fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def _load_image_bytes(image_input: str) -> bytes:
    payload = image_input.strip()
    if payload.startswith(("http://", "https://")):
        response = requests.get(payload, timeout=20)
        response.raise_for_status()
        return response.content
    if payload.startswith("data:image"):
        payload = payload.split(",", 1)[1]
    try:
        return base64.b64decode(payload, validate=True)
    except Exception:
        path = Path(payload)
        if path.exists():
            return path.read_bytes()
        raise ValueError("Invalid image payload: not valid base64 and path not found")


def _decode_bgr_image(image_input: str) -> np.ndarray:
    raw = _load_image_bytes(image_input)
    arr = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Failed to decode image")
    return image


def _encode_bgr_image(image_bgr: np.ndarray) -> str:
    ok, buffer = cv2.imencode(".jpg", image_bgr)
    if not ok:
        raise ValueError("Failed to encode annotated image")
    return base64.b64encode(buffer.tobytes()).decode("utf-8")


def _detect_faces(app: FaceAnalysis, image_bgr: np.ndarray) -> List[Any]:
    faces = app.get(image_bgr)
    return sorted(
        faces,
        key=lambda face: float((face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1])),
        reverse=True,
    )


def _average_embeddings(embeddings: List[np.ndarray]) -> np.ndarray:
    if not embeddings:
        raise ValueError("No usable face embeddings were collected")
    stacked = np.vstack([normalize_embedding(embedding) for embedding in embeddings])
    return normalize_embedding(stacked.mean(axis=0))


def _load_database() -> Dict[str, np.ndarray]:
    with DB_LOCK:
        return load_database(DB_PATH)


def _save_database(database: Dict[str, np.ndarray]) -> None:
    with DB_LOCK:
        save_database(DB_PATH, database)


def _ensure_face_app() -> FaceAnalysis:
    if FACE_APP is None:
        raise HTTPException(status_code=503, detail="Face model not initialized")
    return FACE_APP


def _collect_embeddings(image_inputs: List[str]) -> tuple[List[np.ndarray], int]:
    app = _ensure_face_app()
    embeddings: List[np.ndarray] = []
    used = 0

    for image_input in image_inputs:
        image = _decode_bgr_image(image_input)
        with INFER_LOCK:
            faces = _detect_faces(app, image)
        if not faces:
            continue
        embeddings.append(np.asarray(faces[0].embedding, dtype=np.float32))
        used += 1

    if not embeddings:
        raise ValueError("No face detected from provided images")
    return embeddings, used


def _recognize_faces(image_bgr: np.ndarray, threshold: float) -> tuple[List[FaceMatchResult], np.ndarray]:
    app = _ensure_face_app()
    database = _load_database()

    with INFER_LOCK:
        faces = _detect_faces(app, image_bgr)

    annotated = image_bgr.copy()
    results: List[FaceMatchResult] = []
    for face in faces:
        bbox = [int(v) for v in face.bbox.astype(int).tolist()]
        name, score = match_embedding(face.embedding, database, threshold)
        det_score = float(getattr(face, "det_score", 0.0))
        label = f"{name} {score:.2f}" if name != "Unknown" else "Unknown"

        cv2.rectangle(annotated, (bbox[0], bbox[1]), (bbox[2], bbox[3]), (0, 255, 0), 2)
        annotated = _put_text(annotated, label, (bbox[0], max(0, bbox[1] - 28)), (0, 255, 0))

        results.append(
            FaceMatchResult(
                bbox=bbox,
                name=name,
                match_score=float(score),
                det_score=det_score,
            )
        )

    return results, annotated


@asynccontextmanager
async def lifespan(app: FastAPI):
    global FACE_APP
    face_app = FaceAnalysis(name=DEFAULT_MODEL_NAME)
    face_app.prepare(ctx_id=CTX_ID, det_size=(DEFAULT_DET_SIZE, DEFAULT_DET_SIZE))
    FACE_APP = face_app
    yield
    FACE_APP = None


app = FastAPI(
    title="AbotClaw Face Recognition Service",
    version=SERVICE_VERSION,
    description="InsightFace-based HTTP face recognition service.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> Dict[str, Any]:
    database = _load_database()
    return {
        "status": "ok",
        "version": SERVICE_VERSION,
        "ctx_id": CTX_ID,
        "model_name": DEFAULT_MODEL_NAME,
        "det_size": DEFAULT_DET_SIZE,
        "db_path": str(DB_PATH),
        "people_count": len(database),
        "model_loaded": FACE_APP is not None,
    }


@app.get("/face/people", response_model=PeopleListResponse)
def list_people() -> PeopleListResponse:
    database = _load_database()
    people = sorted(database)
    return PeopleListResponse(count=len(people), people=people)


@app.post("/face/enroll", response_model=EnrollResponse)
def enroll_person(req: EnrollRequest) -> EnrollResponse:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name cannot be empty")

    try:
        embeddings, used = _collect_embeddings(req.images)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image payload: {exc}")

    database = _load_database()
    database[name] = _average_embeddings(embeddings)
    _save_database(database)
    return EnrollResponse(
        name=name,
        samples_received=len(req.images),
        samples_used=used,
        total_people=len(database),
        db_path=str(DB_PATH),
    )


@app.post("/face/enroll/batch", response_model=BatchEnrollResponse)
def batch_enroll(req: BatchEnrollRequest) -> BatchEnrollResponse:
    database = _load_database()
    results: List[BatchEnrollItemResult] = []

    for person in req.people:
        name = person.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="person.name cannot be empty")
        try:
            embeddings, used = _collect_embeddings(person.images)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{name}: {exc}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{name}: invalid image payload: {exc}")

        database[name] = _average_embeddings(embeddings)
        results.append(
            BatchEnrollItemResult(
                name=name,
                samples_received=len(person.images),
                samples_used=used,
            )
        )

    _save_database(database)
    return BatchEnrollResponse(
        total_people=len(database),
        db_path=str(DB_PATH),
        results=results,
    )


@app.post("/face/recognize", response_model=RecognizeResponse)
def recognize(req: RecognizeRequest) -> RecognizeResponse:
    start_t = time.time()
    try:
        image_bgr = _decode_bgr_image(req.image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image payload: {exc}")

    try:
        results, annotated = _recognize_faces(image_bgr, req.threshold)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Recognition failed: {exc}")

    latency_ms = (time.time() - start_t) * 1000.0
    annotated_image = _encode_bgr_image(annotated) if req.include_annotated_image else None
    return RecognizeResponse(
        count=len(results),
        threshold=req.threshold,
        results=results,
        latency_ms=latency_ms,
        annotated_image=annotated_image,
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", str(DEFAULT_PORT)))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
