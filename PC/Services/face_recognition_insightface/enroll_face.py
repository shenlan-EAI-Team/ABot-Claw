"""Standalone face enrollment script - no API required.

Usage:
    .venv/bin/python enroll_face.py --name "张三" --image /path/to/photo.jpg

    # Register from multiple images (embeddings are averaged)
    .venv/bin/python enroll_face.py --name "李四" --image img1.jpg --image img2.jpg --image img3.jpg

    # Register multiple people at once
    .venv/bin/python enroll_face.py --batch names.txt

    # Dry run - show what would be written without modifying the database
    .venv/bin/python enroll_face.py --name "王五" --image photo.jpg --dry-run

The script reads/writes the same face_db.json used by the HTTP service,
so changes take effect immediately without restarting the service.

Note: This script auto-detects GPU (CUDA) and sets LD_LIBRARY_PATH internally.
Use --device=cpu to force CPU mode if needed.
"""

from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENV_NVIDIA = os.path.join(_SCRIPT_DIR, ".venv", "lib", "python3.13", "site-packages", "nvidia")
_CUDNN_LIB = os.path.join(_VENV_NVIDIA, "cudnn", "lib")
_CUBLAS_LIB = os.path.join(_VENV_NVIDIA, "cublas", "lib")

_NEEDED_LIBS = f"{_CUDNN_LIB}:{_CUBLAS_LIB}"
if _CUDNN_LIB not in os.environ.get("LD_LIBRARY_PATH", ""):
    _new_ld = f"{_NEEDED_LIBS}:{os.environ.get('LD_LIBRARY_PATH', '')}"
    os.environ["LD_LIBRARY_PATH"] = _new_ld
    os.execvpe(sys.executable, [sys.executable, __file__] + sys.argv[1:], os.environ)

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from insightface.app import FaceAnalysis

BASE_DIR = Path(_SCRIPT_DIR)
DEFAULT_DB_PATH = BASE_DIR / "data" / "face_db.json"
DEFAULT_MODEL_NAME = "buffalo_l"
DEFAULT_DET_SIZE = 640


def resolve_ctx_id() -> int:
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


def normalize_embedding(embedding) -> np.ndarray:
    vector = np.asarray(embedding, dtype=np.float32).flatten()
    norm = np.linalg.norm(vector)
    if norm == 0:
        raise ValueError("Embedding norm is zero.")
    return vector / norm


def load_database(db_path: Path) -> dict[str, np.ndarray]:
    if not db_path.exists():
        return {}
    payload = json.loads(db_path.read_text(encoding="utf-8"))
    database: dict[str, np.ndarray] = {}
    for item in payload.get("people", []):
        name = item.get("name")
        embedding = item.get("embedding")
        if not name or embedding is None:
            continue
        database[name] = normalize_embedding(np.asarray(embedding, dtype=np.float32))
    return database


def save_database(db_path: Path, database: dict[str, np.ndarray]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "people": [
            {
                "name": name,
                "embedding": normalize_embedding(embedding).tolist(),
            }
            for name, embedding in sorted(database.items())
        ]
    }
    db_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_embedding_from_image(app: FaceAnalysis, image_path: str) -> np.ndarray:
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Failed to load image: {image_path}")

    faces = app.get(image)
    if not faces:
        raise ValueError(f"No face detected in: {image_path}")

    # Pick the largest face
    best = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    embedding = np.asarray(best.embedding, dtype=np.float32)
    return normalize_embedding(embedding)


def average_embeddings(embeddings: list[np.ndarray]) -> np.ndarray:
    stacked = np.vstack([normalize_embedding(e) for e in embeddings])
    return normalize_embedding(stacked.mean(axis=0))


def enroll_person(
    db_path: Path,
    name: str,
    image_paths: list[str],
    dry_run: bool = False,
) -> dict:
    ctx_id = resolve_ctx_id()
    print(f"Loading InsightFace model ({DEFAULT_MODEL_NAME})... ctx_id={ctx_id}", file=sys.stderr)
    app = FaceAnalysis(name=DEFAULT_MODEL_NAME)
    app.prepare(ctx_id=ctx_id, det_size=(DEFAULT_DET_SIZE, DEFAULT_DET_SIZE))

    embeddings: list[np.ndarray] = []
    errors: list[str] = []

    for path in image_paths:
        try:
            emb = extract_embedding_from_image(app, path)
            embeddings.append(emb)
            print(f"  [OK] {path}: embedding extracted (norm={np.linalg.norm(emb):.4f})", file=sys.stderr)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            print(f"  [SKIP] {path}: {exc}", file=sys.stderr)

    if not embeddings:
        raise RuntimeError(f"No valid embeddings collected for '{name}'. Errors:\n  " + "\n  ".join(errors))

    final_embedding = average_embeddings(embeddings)

    database = {} if dry_run else load_database(db_path)
    database[name] = final_embedding

    if dry_run:
        print(f"\n[DRY RUN] Would write '{name}' with {len(embeddings)} sample(s) to {db_path}", file=sys.stderr)
        print(f"  Embedding dims: {len(final_embedding)}, norm: {np.linalg.norm(final_embedding):.4f}", file=sys.stderr)
        print(f"  First 8 values: {final_embedding[:8].tolist()}", file=sys.stderr)
        return {"dry_run": True, "name": name, "samples_used": len(embeddings)}

    save_database(db_path, database)
    print(f"\n[OK] '{name}' enrolled with {len(embeddings)} sample(s). Total people: {len(database)}", file=sys.stderr)
    return {
        "name": name,
        "samples_used": len(embeddings),
        "total_people": len(database),
        "db_path": str(db_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enroll faces into the database without the HTTP API.")
    parser.add_argument("--name", help="Person name to enroll")
    parser.add_argument("--image", dest="images", action="append", default=[], help="Image file (can be specified multiple times)")
    parser.add_argument("--batch", type=Path, help="Text file, one line per person: 'name|/path/to/image1.jpg|/path/to/image2.jpg'")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="Path to face_db.json")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written without modifying the database")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Batch mode: read from file
    if args.batch:
        if not args.batch.exists():
            print(f"Error: batch file not found: {args.batch}", file=sys.stderr)
            return 1
        ctx_id = resolve_ctx_id()
        print(f"Loading InsightFace model ({DEFAULT_MODEL_NAME})... ctx_id={ctx_id}", file=sys.stderr)
        app = FaceAnalysis(name=DEFAULT_MODEL_NAME)
        app.prepare(ctx_id=ctx_id, det_size=(DEFAULT_DET_SIZE, DEFAULT_DET_SIZE))

        database = {} if args.dry_run else load_database(args.db_path)
        lines = [ln.strip() for ln in args.batch.read_text(encoding="utf-8").splitlines() if ln.strip()]
        results = []

        for line in lines:
            parts = line.split("|")
            if len(parts) < 2:
                print(f"[WARN] Skipping invalid line: {line}", file=sys.stderr)
                continue
            name = parts[0].strip()
            paths = parts[1:]
            embeddings = []
            for path in paths:
                try:
                    emb = extract_embedding_from_image(app, path)
                    embeddings.append(emb)
                except Exception as exc:
                    print(f"  [SKIP] {path}: {exc}", file=sys.stderr)
            if not embeddings:
                print(f"[WARN] No valid embeddings for '{name}', skipping", file=sys.stderr)
                continue
            database[name] = average_embeddings(embeddings)
            results.append(f"{name}: {len(embeddings)} sample(s)")

        if args.dry_run:
            print(f"\n[DRY RUN] Would write {len(results)} person(s) to {args.db_path}", file=sys.stderr)
        else:
            save_database(args.db_path, database)
            print(f"\n[OK] Batch complete. {len(results)} person(s) enrolled. Total: {len(database)}", file=sys.stderr)
        for r in results:
            print(f"  {r}", file=sys.stderr)
        return 0

    # Single person mode
    if not args.name:
        print("Error: --name is required (or use --batch for batch enrollment)", file=sys.stderr)
        return 1
    if not args.images:
        print("Error: at least one --image is required", file=sys.stderr)
        return 1

    result = enroll_person(args.db_path, args.name, args.images, args.dry_run)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
