"""Live YOLO object-detection viewer for the robot D455 camera via ZMQ.

Receives JPEG frames streamed over ZMQ from the robot camera (address from
--camera-zmq-addr) and runs YOLO detection on each frame in a background
thread, then displays annotated results in an OpenCV window.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENV_NVIDIA = os.path.join(_SCRIPT_DIR, ".venv", "lib", "python3.11", "site-packages", "nvidia")
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
        os.environ["LD_LIBRARY_PATH"] = _new_ld

import cv2
import numpy as np
import zmq
from PIL import Image, ImageDraw, ImageFont

_CJK_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

# Accent colour (BGR for OpenCV) — matches face-recognition UI style
_ACCENT_GREEN = (130, 220, 0)   # #00DC82
_ACCENT_GREEN_RGB = (_ACCENT_GREEN[2], _ACCENT_GREEN[1], _ACCENT_GREEN[0])
_STATUS_BG = (18, 18, 18)
_STATUS_FG = _ACCENT_GREEN

# -------------------------------------------------------------------------- #
# Colour palette for the 80 COCO classes (BGR for cv2, RGB for PIL).
# -------------------------------------------------------------------------- #
_COCO_PALETTE_BGR: list[tuple[int, int, int]] = [
    ( 75,  82, 216), (137, 220,   0), ( 59,  63, 163), (137, 142,  58),
    (221,  58, 149), ( 35, 120, 107), ( 59, 201, 181), (203,  27,  89),
    ( 25, 149, 197), (173,  90,  86), (117, 108, 123), ( 55,  94, 206),
    ( 77,  51, 159), (132, 202, 182), ( 84, 155, 138), ( 60, 191, 103),
    (193,  55, 148), ( 31, 188, 174), (139,  75, 208), ( 46, 159, 152),
    (126, 210,  49), ( 69, 161, 123), ( 78,  77, 201), (110,  80, 104),
    ( 49, 197, 208), (178,  56,  81), ( 34, 111, 138), (212, 192,  86),
    ( 41,  51, 134), (130, 205, 102), ( 95, 155, 206), ( 48,  89, 190),
    ( 79,  32,  80), (183, 206,  68), (185,  62,  60), (118, 185, 205),
    (156, 196, 154), ( 34, 173, 165), ( 76,  58, 201), (200, 154,  56),
    (110, 198, 185), ( 50, 151,  80), ( 68, 191, 204), ( 98, 117, 119),
    ( 76,  73, 193), ( 46,  86,  89), ( 91, 198, 155), ( 47,  66, 153),
    (203,  76, 169), (199, 197,  71), ( 24, 155, 214), (205, 197,  91),
    ( 60, 151,  68), ( 85, 110, 162), (176,  72, 179), ( 92, 205,  94),
    ( 62,  84, 152), (193,  84, 193), ( 87, 165,  90), (122,  75, 203),
    ( 47,  70, 201), ( 61, 174, 110), ( 48, 189,  88), (206, 193, 115),
    ( 25,  89,  89), ( 81, 152, 127), ( 88,  73, 201), (145,  60,  97),
    (140, 186, 165), (179,  79,  97), ( 90, 211, 181), (191, 197, 104),
    ( 81,  77, 205), (152, 139,  63), ( 64,  65, 159), ( 97, 189, 121),
    (107, 190,  87), ( 64,  72, 182), (108, 203, 152), ( 48,  93, 113),
]
_COCO_PALETTE_RGB = [(b, g, r) for r, g, b in _COCO_PALETTE_BGR]

_PIL_FONT: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None


def _get_pil_font(size: int = 20) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    global _PIL_FONT
    if _PIL_FONT is None:
        try:
            _PIL_FONT = ImageFont.truetype(_CJK_FONT_PATH, size)
        except Exception:
            _PIL_FONT = ImageFont.load_default()
    return _PIL_FONT


def _coco_color_bgr(class_id: int) -> tuple[int, int, int]:
    return _COCO_PALETTE_BGR[class_id % len(_COCO_PALETTE_BGR)]


def _coco_color_rgb(class_id: int) -> tuple[int, int, int]:
    return _COCO_PALETTE_RGB[class_id % len(_COCO_PALETTE_RGB)]


def _draw_corner_brackets(
    draw: ImageDraw.ImageDraw,
    x1: int, y1: int, x2: int, y2: int,
    color: tuple[int, int, int],
    corner_px: int = 16,
    thickness: int = 3,
) -> None:
    c = color
    k = corner_px
    # top-left
    draw.line([(x1, y1 + k), (x1, y1), (x1 + k, y1)], fill=c, width=thickness)
    # top-right
    draw.line([(x2 - k, y1), (x2, y1), (x2, y1 + k)], fill=c, width=thickness)
    # bottom-right
    draw.line([(x2, y2 - k), (x2, y2), (x2 - k, y2)], fill=c, width=thickness)
    # bottom-left
    draw.line([(x1 + k, y2), (x1, y2), (x1, y2 - k)], fill=c, width=thickness)


def draw_detections(
    frame: np.ndarray,
    detections: list[dict[str, Any]],
    infer_ms: float,
    fps: float,
    infer_total: int,
    conf_thres: float,
    device: str,
    model_name: str,
) -> np.ndarray:
    h, w = frame.shape[:2]
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font = _get_pil_font(20)

    # ---------- draw detection boxes ---------- #
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cls_id = det.get("class_id", 0)
        conf = det.get("confidence", 0.0)
        name = det.get("class_name", "?")
        rgb = _coco_color_rgb(cls_id)
        bgr = _coco_color_bgr(cls_id)

        xi1, yi1 = max(0, min(int(x1), w - 1)), max(0, min(int(y1), h - 1))
        xi2, yi2 = max(0, min(int(x2), w - 1)), max(0, min(int(y2), h - 1))
        if xi2 <= xi1 or yi2 <= yi1:
            continue

        _draw_corner_brackets(draw, xi1, yi1, xi2, yi2, rgb, corner_px=18, thickness=3)

        # label above the box, clamped to image top
        label = f" {name} {conf:.2f} "
        try:
            tb = draw.textbbox((xi1, yi1), label, font=font)
        except Exception:
            tb = (xi1, yi1, xi1 + 140, yi1 + 26)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]
        lx1 = xi1
        ly1 = max(0, yi1 - th - 6)
        if ly1 < yi1:
            draw.rectangle((lx1, ly1, lx1 + tw, yi1), fill=rgb)
            draw.text((lx1 + 2, ly1), label, font=font, fill=(20, 20, 20))

        # draw bbox line on top of brackets (clean cv2 overlay)
        cv2.rectangle(frame, (xi1, yi1), (xi2, yi2), bgr, 2)

        # label background and text on cv2 (cleaner than PIL for overlays)
        label_bg = bgr
        cv2.rectangle(frame, (xi1, max(0, yi1 - th - 8)), (xi1 + tw, yi1), label_bg, -1)
        cv2.rectangle(frame, (xi1, max(0, yi1 - th - 8)), (xi1 + tw, yi1), bgr, 1)
        cv2.putText(frame, label.strip(), (xi1 + 3, yi1 - th // 2 - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (20, 20, 20), 1, cv2.LINE_AA)

    # ---------- top status bar ---------- #
    bar_h = 42
    status = (
        f"  FPS:{fps:5.1f}  |  infer:{infer_ms:5.0f}ms  |  "
        f"detections:{len(detections)}  |  total:{infer_total}  |  "
        f"conf:{conf_thres:.2f}  |  {model_name}  |  {device}  "
    )
    cv2.rectangle(frame, (0, 0), (w - 1, bar_h), _STATUS_BG, -1)
    cv2.rectangle(frame, (0, 0), (w - 1, bar_h), _ACCENT_GREEN, 1)
    cv2.putText(frame, status, (10, 27),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, _STATUS_BG, 1, cv2.LINE_AA)

    # ---------- bottom class legend ---------- #
    if detections:
        seen: dict[int, dict[str, Any]] = {}
        for det in detections:
            cid = det["class_id"]
            if cid not in seen or det["confidence"] > seen[cid]["confidence"]:
                seen[cid] = det

        legend_items = sorted(seen.values(), key=lambda d: -d["confidence"])
        num_items = len(legend_items)
        item_h = 28
        legend_h = num_items * item_h + 10
        legend_y = h - legend_h

        cv2.rectangle(frame, (0, legend_y), (280, h - 1), (10, 10, 10), -1)
        cv2.rectangle(frame, (0, legend_y), (280, h - 1), _ACCENT_GREEN, 1)

        for i, det in enumerate(legend_items):
            cy = legend_y + 6 + i * item_h
            cid = det["class_id"]
            name = det["class_name"]
            conf = det["confidence"]
            color = _coco_color_bgr(cid)
            dot_x = 10
            cv2.circle(frame, (dot_x, cy + 9), 5, color, -1)
            tag = f"  {name}  {conf:.2f}"
            cv2.putText(frame, tag, (dot_x + 14, cy + 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (220, 220, 220), 1, cv2.LINE_AA)

    return frame


# -------------------------------------------------------------------------- #
# ZMQ frame source — identical to face-recognition realtime_view.py
# -------------------------------------------------------------------------- #
class ZmqFrameSource:
    HEADER_SIZE = struct.calcsize("<dI")

    def __init__(self, zmq_addr: str):
        self._zmq_addr = zmq_addr
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
            payload = raw[self.HEADER_SIZE:]
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


# -------------------------------------------------------------------------- #
# YOLO inference worker — runs in a background thread
# -------------------------------------------------------------------------- #
class YoloWorker:
    def __init__(
        self,
        frame_source: ZmqFrameSource,
        model_path: str,
        conf_thres: float,
        iou_thres: float,
        device: str,
        imgsz: int,
    ):
        self._source = frame_source
        self._model_path = model_path
        self._conf_thres = conf_thres
        self._iou_thres = iou_thres
        self._device = device
        self._imgsz = imgsz
        self._running = False
        self._thread: threading.Thread | None = None
        self._latest_frame: np.ndarray | None = None
        self._latest_jpg: bytes = b""
        self._latest_detections: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._infer_count = 0
        self._infer_lock = threading.Lock()
        self._last_infer_time = time.time()
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return
        import yolov5
        print(f"[realtime_yolo] Loading YOLO model: {self._model_path}  device={self._device}",
              flush=True)
        self._model = yolov5.load(self._model_path, device=self._device)
        print("[realtime_yolo] YOLO model loaded.", flush=True)

    @property
    def model_name(self) -> str:
        return Path(self._model_path).stem

    def _infer(self, frame: np.ndarray) -> list[dict[str, Any]]:
        self._ensure_model()
        results = self._model(frame)
        detections = []
        for *xyxy, conf, cls in results.pred[0].cpu().numpy():
            cls_id = int(cls)
            name = str(results.names[cls_id])
            detections.append({
                "bbox": [float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])],
                "confidence": float(conf),
                "class_id": cls_id,
                "class_name": name,
            })
        return detections

    def start(self) -> "YoloWorker":
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
                detections = self._infer(frame)
                infer_ms = (time.time() - t0) * 1000
                with self._lock:
                    self._latest_frame = frame
                    self._latest_jpg = jpg_bytes
                    self._latest_detections = detections
                with self._infer_lock:
                    self._infer_count += 1
                self._last_infer_time = time.time()
                if detections:
                    top = max(detections, key=lambda d: d["confidence"])
                    print(f"[ALERT] {top['class_name']}  conf={top['confidence']:.2f}  infer={infer_ms:.0f}ms",
                          flush=True)
            except Exception as exc:
                print(f"[realtime_yolo] Inference error: {exc}", flush=True)

    def get_latest(self) -> tuple[np.ndarray | None, bytes, list[dict[str, Any]], int, float]:
        with self._lock:
            frame = self._latest_frame
            jpg = self._latest_jpg
            detections = list(self._latest_detections)
        with self._infer_lock:
            count = self._infer_count
        infer_ms = (time.time() - self._last_infer_time) * 1000
        return frame, jpg, detections, count, infer_ms

    def close(self) -> None:
        self._running = False
        if self._thread:
            try:
                self._thread.join(timeout=2)
            except KeyboardInterrupt:
                pass


# -------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    default_model = str(Path(_SCRIPT_DIR) / "yolov5l6.pt")
    default_zmq = "tcp://192.168.123.164:5555"

    parser = argparse.ArgumentParser(
        description="Live YOLO detection viewer — D455 camera via ZMQ.",
    )
    parser.add_argument(
        "--camera-zmq-addr",
        default=default_zmq,
        help=f"ZMQ D455 colour-stream address (default: {default_zmq})",
    )
    parser.add_argument(
        "--model",
        default=default_model,
        help=f"Path to YOLO weights file (default: {default_model})",
    )
    parser.add_argument(
        "--conf-thres",
        type=float,
        default=0.25,
        help="Confidence threshold (default: 0.25)",
    )
    parser.add_argument(
        "--iou-thres",
        type=float,
        default=0.45,
        help="NMS IoU threshold (default: 0.45)",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Inference image size (default: 640)",
    )
    parser.add_argument(
        "--device",
        default=os.getenv("DEVICE", "auto"),
        help="Device: 'auto', 'cuda', 'cpu', or 'cuda:0' (default: auto)",
    )
    parser.add_argument(
        "--window",
        default="ABot-Claw YOLO Detection",
        help="OpenCV window title",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Resolve device
    device = args.device.strip().lower()
    if device == "auto":
        import torch
        device = "cuda:0" if torch.cuda.is_available() else "cpu"

    frame_source = ZmqFrameSource(args.camera_zmq_addr)
    frame_source.start()

    yolo_worker = YoloWorker(
        frame_source=frame_source,
        model_path=args.model,
        conf_thres=args.conf_thres,
        iou_thres=args.iou_thres,
        device=device,
        imgsz=args.imgsz,
    )
    yolo_worker.start()

    # Pre-warm model so it loads before the main loop
    yolo_worker._ensure_model()

    # Trackbar shared state
    _conf_value = [args.conf_thres]  # list so inner function can modify

    def on_conf_trackbar(val: int) -> None:
        _conf_value[0] = val / 100.0

    try:
        cv2.startWindowThread()
    except Exception:
        pass
    cv2.namedWindow(args.window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(args.window, 960, 720)

    # Top-left overlay showing key info, used for quick orientation on placeholder
    placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
    last_time = time.time()
    last_frame = placeholder.copy()

    cv2.imshow(args.window, placeholder)
    cv2.waitKey(30)

    # Attach trackbar AFTER first.imshow so the window exists
    trackbar_name = "conf*100"
    cv2.createTrackbar(trackbar_name, args.window, int(args.conf_thres * 100), 90, on_conf_trackbar)

    print(
        f"[realtime_yolo] window shown, zmq_addr={args.camera_zmq_addr}, "
        f"model={args.model}, device={device}, conf={args.conf_thres}, "
        f"entering loop...",
        flush=True,
    )

    try:
        frame_count = 0
        while True:
            # Update conf_thres from trackbar position
            conf_thres = _conf_value[0]

            frame, jpg_bytes, detections, infer_count, infer_ms = yolo_worker.get_latest()
            now = time.time()
            fps = 1.0 / max(now - last_time, 1e-6)
            last_time = now

            # Filter detections by current conf_thres for display
            visible = [d for d in detections if d["confidence"] >= conf_thres]

            if frame is not None:
                last_frame = frame
                if jpg_bytes:
                    display = draw_detections(
                        last_frame, visible, infer_ms, fps, infer_count,
                        conf_thres=conf_thres,
                        device=device,
                        model_name=yolo_worker.model_name,
                    )
                else:
                    display = last_frame.copy()
            else:
                display = placeholder.copy()

            frame_count += 1
            if frame_count <= 5 or frame_count % 60 == 0:
                h = hashlib.md5(last_frame.tobytes()).hexdigest()[:8]
                print(
                    f"[realtime_yolo] frame={frame_count} infer_total={infer_count} "
                    f"fps={fps:.1f} visible={len(visible)}/{len(detections)} hash={h}",
                    flush=True,
                )

            cv2.imshow(args.window, display)
            key = cv2.waitKey(30) & 0xFF
            if key in (27, ord("q")):
                break

    finally:
        yolo_worker.close()
        frame_source.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
