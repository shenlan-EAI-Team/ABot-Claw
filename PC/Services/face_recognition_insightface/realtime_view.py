"""Live face-recognition viewer for the robot camera.

Supports two inference modes:
  --mode api     : call face service HTTP API (requires running face service)
  --mode local   : run InsightFace directly, no service needed (default)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

# Prepend venv nvidia library paths so onnxruntime can find cuDNN/CUDA libs.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENV_NVIDIA = os.path.join(_SCRIPT_DIR, ".venv", "lib", "python3.13", "site-packages", "nvidia")
_CUDNN_LIB = os.path.join(_VENV_NVIDIA, "cudnn", "lib")
_CUBLAS_LIB = os.path.join(_VENV_NVIDIA, "cublas", "lib")
if _CUDNN_LIB not in os.environ.get("LD_LIBRARY_PATH", ""):
    _new_ld = f"{_CUDNN_LIB}:{_CUBLAS_LIB}:{os.environ.get('LD_LIBRARY_PATH', '')}"
    _env = dict(os.environ)
    _env["LD_LIBRARY_PATH"] = _new_ld
    _venv_python = os.path.join(_SCRIPT_DIR, ".venv", "bin", "python3")
    if os.path.exists(_venv_python):
        os.execve(_venv_python, [_venv_python, __file__] + sys.argv[1:], _env)
    else:
        # Fallback: use current python with updated env
        os.environ["LD_LIBRARY_PATH"] = _new_ld

import cv2
import numpy as np
import zmq
from PIL import Image, ImageDraw, ImageFont

from face_db import load_database, match_embedding, normalize_embedding

_CJK_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def default_agent_server_root() -> Path:
    return Path(__file__).resolve().parents[2] / "robot_client" / "unitree_G1" / "agent_server"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch live robot frames, run face recognition, and display annotated results."
    )
    parser.add_argument(
        "--camera-source",
        choices=("auto", "sdk", "http", "zmq"),
        default="auto",
        help="Frame source: direct G1 SDK, agent-server HTTP, ZMQ D455 camera, or auto fallback.",
    )
    parser.add_argument(
        "--agent-server-root",
        default=str(default_agent_server_root()),
        help="Path to unitree_G1/agent_server for importing robot_sdk.",
    )
    parser.add_argument(
        "--camera-host",
        default=None,
        help="Optional robot IP override for direct SDK mode.",
    )
    parser.add_argument(
        "--camera-url",
        default="http://127.0.0.1:8002/camera/rgb.jpg",
        help="Agent server RGB camera endpoint for HTTP mode.",
    )
    parser.add_argument(
        "--camera-zmq-addr",
        default="tcp://127.0.0.1:5555",
        help="ZMQ D455 color stream address for ZMQ mode.",
    )
    parser.add_argument(
        "--mode",
        choices=("local", "api"),
        default="local",
        help="'local' = direct InsightFace inference, no service needed. "
             "'api' = call face recognition HTTP service.",
    )
    parser.add_argument(
        "--face-url",
        default="http://127.0.0.1:8016",
        help="Face recognition service base URL (used when --mode=api).",
    )
    parser.add_argument(
        "--db-path",
        default=str(Path(_SCRIPT_DIR) / "data" / "face_db.json"),
        help="Path to face_db.json (used when --mode=local).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.45,
        help="Face match threshold.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=5.0,
        help="Target refresh rate.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--window",
        default="ABot-Claw Face Recognition",
        help="OpenCV window title.",
    )
    return parser.parse_args()


_PIL_FONT: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None


def _get_pil_font(size: int = 20) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    global _PIL_FONT
    if _PIL_FONT is None:
        try:
            _PIL_FONT = ImageFont.truetype(_CJK_FONT_PATH, size)
        except Exception:
            _PIL_FONT = ImageFont.load_default()
    return _PIL_FONT


def draw_face_boxes(
    frame: np.ndarray,
    results: list[dict[str, Any]],
    count: int,
    infer_ms: float,
    fps: float,
    infer_total: int,
) -> np.ndarray:
    h, w = frame.shape[:2]
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font = _get_pil_font(20)

    for res in results:
        bbox = res.get("bbox", [])
        if len(bbox) != 4:
            continue
        x1, y1, x2, y2 = bbox
        name = res.get("name", "Unknown")
        score = res.get("match_score", 0.0)

        color = (0, 200, 0) if name != "Unknown" else (128, 128, 255)
        label = f"{name} {score:.2f}" if name != "Unknown" else "Unknown"

        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w - 1, x2), min(h - 1, y2)
        draw.rectangle([x1, y1, x2, y2], outline=(color[2], color[1], color[0]), width=2)

        try:
            tb = draw.textbbox((x1, y1), label, font=font)
        except Exception:
            tb = (x1, y1, x1 + 100, y1 + 30)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        ly2 = max(0, y1 - th - 8)
        if ly2 < y1:
            draw.rectangle((x1, ly2, x1 + tw + 12, y1), fill=(color[2], color[1], color[0]))
            draw.text((x1 + 6, ly2), label, font=font, fill=(255, 255, 255))

    status = f"faces={count} infer={infer_ms:.0f}ms fps={fps:.1f} total={infer_total}"
    draw.rectangle((10, 10, 720, 70), fill=(20, 20, 20))
    draw.text((20, 20), status, font=font, fill=(0, 200, 0))

    output = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return output


class ZmqFrameSource:
    """Continuously drain ZMQ and keep only the latest frame in a thread-safe buffer."""

    HEADER_SIZE = struct.calcsize("<dI")

    def __init__(self, zmq_addr: str, timeout: float):
        self._zmq_addr = zmq_addr
        self._timeout = timeout
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.connect(zmq_addr)
        self._socket.setsockopt(zmq.SUBSCRIBE, b"")
        self._socket.setsockopt(zmq.RCVTIMEO, 500)
        self._socket.setsockopt(zmq.RCVHWM, 1)
        self._running = False
        self._thread: threading.Thread | None = None
        self._latest: tuple[np.ndarray, bytes] | None = None
        self._lock = threading.Lock()
        self._placeholder = np.zeros((480, 640, 3), dtype=np.uint8)

    def start(self) -> "ZmqFrameSource":
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while self._running:
            try:
                raw = self._socket.recv()
            except zmq.Again:
                continue
            header = raw[: self.HEADER_SIZE]
            payload = raw[self.HEADER_SIZE :]
            frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            with self._lock:
                self._latest = (frame, payload)

    def get_latest(self) -> tuple[np.ndarray, bytes]:
        with self._lock:
            if self._latest is None:
                return self._placeholder, b""
            return self._latest

    def close(self) -> None:
        self._running = False
        if self._thread:
            try:
                self._thread.join(timeout=2)
            except KeyboardInterrupt:
                pass
        self._socket.close()
        self._context.term()


class InferenceWorker:
    """Background thread: grab latest frame from source, run face recognition, store result."""

    def __init__(
        self,
        frame_source: ZmqFrameSource,
        mode: str,
        face_url: str,
        db_path: str,
        threshold: float,
        timeout: float,
    ):
        self._source = frame_source
        self._mode = mode
        self._face_url = face_url
        self._threshold = threshold
        self._timeout = timeout
        self._running = False
        self._thread: threading.Thread | None = None
        self._latest_frame: np.ndarray | None = None
        self._latest_jpg: bytes = b""
        self._latest_result: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._infer_count = 0
        self._infer_lock = threading.Lock()
        self._last_infer_time = time.time()
        self._seen_names: set[str] = set()

        # Local-mode components
        self._face_app = None
        self._database: dict[str, np.ndarray] = {}
        self._db_path = db_path

        # API-mode session
        self._session = None

    def _ensure_face_app(self) -> "FaceAnalysis":
        if self._face_app is not None:
            return self._face_app
        import onnxruntime as ort
        from insightface.app import FaceAnalysis

        value = (os.getenv("DEVICE") or os.getenv("FACE_RECOGNITION_CTX_ID") or "auto").strip().lower()
        providers = set(ort.get_available_providers())
        if value == "auto":
            ctx_id = 0 if "CUDAExecutionProvider" in providers else -1
        elif value in {"cpu", "-1"}:
            ctx_id = -1
        elif value == "cuda":
            ctx_id = 0 if "CUDAExecutionProvider" in providers else -1
        else:
            try:
                ctx_id = int(value)
            except ValueError:
                ctx_id = -1

        model_name = os.getenv("FACE_RECOGNITION_MODEL", "buffalo_l")
        det_size = int(os.getenv("FACE_RECOGNITION_DET_SIZE", "640"))
        print(f"[realtime_view] Loading InsightFace ({model_name})... ctx_id={ctx_id}", flush=True)
        self._face_app = FaceAnalysis(name=model_name)
        self._face_app.prepare(ctx_id=ctx_id, det_size=(det_size, det_size))
        print("[realtime_view] InsightFace loaded.", flush=True)

    def _load_database(self) -> dict[str, np.ndarray]:
        return load_database(Path(self._db_path))

    def _detect_faces(self, image_bgr: np.ndarray) -> list:
        app = self._ensure_face_app()
        return sorted(
            app.get(image_bgr),
            key=lambda f: float((f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])),
            reverse=True,
        )

    def _recognize_local(self, image_bgr: np.ndarray) -> dict[str, Any]:
        self._database = self._load_database()
        faces = self._detect_faces(image_bgr)
        results = []
        for face in faces:
            bbox = [int(v) for v in face.bbox.astype(int).tolist()]
            name, score = match_embedding(face.embedding, self._database, self._threshold)
            det_score = float(getattr(face, "det_score", 0.0))
            results.append({
                "bbox": bbox,
                "name": name,
                "match_score": float(score),
                "det_score": det_score,
            })
        return {"count": len(results), "results": results}

    def _recognize_api(self, image_bytes: bytes) -> dict[str, Any]:
        if self._session is None:
            import requests
            self._session = requests.Session()
        payload = {
            "image": base64.b64encode(image_bytes).decode("utf-8"),
            "threshold": self._threshold,
            "include_annotated_image": False,
        }
        response = self._session.post(
            f"{self._face_url.rstrip('/')}/face/recognize",
            json=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()

    def start(self) -> "InferenceWorker":
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while self._running:
            frame, jpg_bytes = self._source.get_latest()
            if not jpg_bytes:
                time.sleep(0.01)
                continue
            try:
                t0 = time.time()
                if self._mode == "local":
                    result = self._recognize_local(frame)
                else:
                    result = self._recognize_api(jpg_bytes)
                infer_latency = (time.time() - t0) * 1000

                results = result.get("results", [])
                for res in results:
                    name = res.get("name", "")
                    if name and name != "Unknown" and name not in self._seen_names:
                        self._seen_names.add(name)
                        print(f"[ALERT] Recognized: {name}  (score={res.get('match_score', 0):.2f}, infer={infer_latency:.0f}ms)", flush=True)

                with self._lock:
                    self._latest_frame = frame
                    self._latest_jpg = jpg_bytes
                    self._latest_result = result
                with self._infer_lock:
                    self._infer_count += 1
                self._last_infer_time = time.time()
            except Exception as exc:
                print(f"[realtime_view] Inference error: {exc}", flush=True)

    def get_latest(self) -> tuple[np.ndarray | None, bytes, dict[str, Any], int, float]:
        with self._lock:
            frame = self._latest_frame
            jpg = self._latest_jpg
            result = dict(self._latest_result)
        with self._infer_lock:
            count = self._infer_count
        infer_ms = (time.time() - self._last_infer_time) * 1000
        return frame, jpg, result, count, infer_ms

    def close(self) -> None:
        self._running = False
        if self._thread:
            try:
                self._thread.join(timeout=2)
            except KeyboardInterrupt:
                pass
        if self._session:
            self._session.close()



def main() -> int:
    args = parse_args()

    frame_source = ZmqFrameSource(args.camera_zmq_addr, args.timeout)
    frame_source.start()

    infer_worker = InferenceWorker(
        frame_source=frame_source,
        mode=args.mode,
        face_url=args.face_url,
        db_path=args.db_path,
        threshold=args.threshold,
        timeout=args.timeout,
    )
    infer_worker.start()

    # Pre-warm InsightFace so model loads before the main loop, avoiding
    # a blocking load inside the inference thread during Ctrl+C cleanup.
    if args.mode == "local":
        infer_worker._ensure_face_app()

    try:
        cv2.startWindowThread()
    except Exception:
        pass
    cv2.namedWindow(args.window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(args.window, 960, 720)
    placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
    last_time = time.time()
    last_frame = placeholder.copy()

    cv2.imshow(args.window, placeholder)
    cv2.waitKey(30)
    print(
        f"[realtime_view] window shown, source=zmq, mode={args.mode}, "
        f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM')}, "
        f"DISPLAY={os.environ.get('DISPLAY')}, "
        f"zmq_addr={args.camera_zmq_addr}, "
        f"db_path={args.db_path}, "
        f"entering loop...",
        flush=True,
    )

    try:
        frame_count = 0
        while True:
            # Grab latest processed result (non-blocking).
            frame, jpg_bytes, result, infer_count, infer_ms = infer_worker.get_latest()
            now = time.time()
            fps = 1.0 / max(now - last_time, 1e-6)
            last_time = now

            results = result.get("results", [])
            count = int(result.get("count", 0))
            if frame is not None:
                last_frame = frame
                if jpg_bytes:
                    display = draw_face_boxes(
                        last_frame, results, count, infer_ms, fps, infer_count
                    )
                else:
                    display = last_frame.copy()
            else:
                display = placeholder.copy()
                count = 0

            frame_count += 1
            ok = frame is not None
            if frame_count <= 5 or frame_count % 60 == 0:
                h = hashlib.md5(last_frame.tobytes()).hexdigest()[:8]
                print(
                    f"[realtime_view] frame={frame_count} infer_total={infer_count} "
                    f"fps={fps:.1f} faces={count} hash={h}",
                    flush=True,
                )

            cv2.imshow(args.window, display)
            key = cv2.waitKey(30) & 0xFF
            if key in (27, ord("q")):
                break

    finally:
        infer_worker.close()
        frame_source.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
