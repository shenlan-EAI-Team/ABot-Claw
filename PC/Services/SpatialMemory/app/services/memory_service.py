from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from app.config import settings
from app.embeddings import cosine_similarity, decode_image_input, image_embedding, text_embedding
from app.schemas import (
    KeyframeBatchIngestRequest,
    MemoryResult,
    ObjectMemoryUpsertRequest,
    PlaceMemoryUpsertRequest,
    Pose,
    SemanticFrameIngestRequest,
    UnifiedQuery,
    VisualIndexUpdateRequest,
)
from app.storage import SqliteStore


class PlaceImageDecodeError(ValueError):
    """The supplied place reference image could not be decoded."""


class PlaceImageSaveError(RuntimeError):
    """A decoded place reference image could not be saved."""


class PlaceNotFoundError(LookupError):
    """No place memory exists for the requested ID."""


class PlaceImageNotFoundError(LookupError):
    """The place exists but its reference image cannot be served."""


class PlaceImageHashConflictError(ValueError):
    """The supplied image hash does not identify the place's current JPEG."""


class MemoryService:
    def __init__(self, store: SqliteStore):
        self.store = store
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.image_dir.mkdir(parents=True, exist_ok=True)

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": settings.service_name,
            "version": settings.version,
            "records": self.store.count_memories(),
            "data_dir": str(settings.data_dir),
        }

    def _save_image(self, image: Image.Image, memory_id: str) -> str:
        path = settings.image_dir / f"{memory_id}.jpg"
        image.convert("RGB").save(path, format="JPEG", quality=90)
        return str(path)

    @staticmethod
    def _safe_json_object(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as image_file:
            for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _resolve_image_path(self, stored_path: Any) -> Optional[Path]:
        """Resolve only configured-image-dir files, including the Docker legacy prefix."""
        if not stored_path or not isinstance(stored_path, str):
            return None

        configured_dir = settings.image_dir.resolve()
        candidate = Path(stored_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if resolved.is_relative_to(configured_dir):
                return resolved

            legacy_dir = Path("/services/SpatialMemory/data/images")
            if resolved.parent == legacy_dir:
                mapped = (configured_dir / resolved.name).resolve()
                if mapped.is_relative_to(configured_dir):
                    return mapped
            return None

        if candidate.parent in {Path("images"), Path("data/images")}:
            resolved = (configured_dir / candidate.name).resolve()
            if resolved.is_relative_to(configured_dir):
                return resolved
        return None

    def _image_sha256_for_row(self, row: dict[str, Any]) -> Optional[str]:
        path = self._resolve_image_path(row.get("image_path"))
        if path is None or not path.is_file():
            return None
        try:
            return self._sha256_file(path)
        except OSError:
            return None

    def _visual_index_for_row(
        self,
        row: dict[str, Any],
        extra: Optional[dict[str, Any]] = None,
        image_sha256: Optional[str] = None,
    ) -> dict[str, Any]:
        place_id = str(row["id"])
        safe_extra = extra if extra is not None else self._safe_json_object(row.get("extra_json"))
        stored = safe_extra.get("visual_index")
        if not isinstance(stored, dict):
            stored = {}

        if image_sha256 is None:
            image_sha256 = self._image_sha256_for_row(row)
        result = {
            "status": "not_indexed",
            "image_id": place_id,
            "image_sha256": image_sha256,
            "backend": None,
            "version": None,
            "updated_at": None,
            "error": None,
        }
        for key in result:
            if key in stored:
                result[key] = stored[key]
        if result["status"] not in {"not_indexed", "pending", "indexed", "failed", "deleted"}:
            result["status"] = "not_indexed"
        if not result["image_id"]:
            result["image_id"] = place_id
        return result

    def _pose_to_cols(self, pose: Pose) -> dict[str, Any]:
        return {
            "x": pose.x,
            "y": pose.y,
            "z": pose.z,
            "roll": pose.roll,
            "pitch": pose.pitch,
            "yaw": pose.yaw,
            "qx": pose.qx,
            "qy": pose.qy,
            "qz": pose.qz,
            "qw": pose.qw,
        }

    def upsert_object(self, req: ObjectMemoryUpsertRequest) -> dict[str, Any]:
        ts = req.timestamp or time.time()
        memory_id = req.object_id or f"obj_{uuid.uuid4().hex[:12]}"

        image_path: Optional[str] = None
        emb = np.array([], dtype=np.float32)
        if req.image:
            image = decode_image_input(req.image)
            image_path = self._save_image(image, memory_id)
            emb = image_embedding(image, settings.embedding_dim)

        payload = {
            "id": memory_id,
            "memory_type": "object",
            "name": req.object_name,
            "robot_id": req.robot_id,
            "robot_type": req.robot_type,
            "source": req.source,
            "timestamp": ts,
            "timestamp_ns": int(ts * 1e9),
            **self._pose_to_cols(req.object_pose),
            "robot_pose": req.robot_pose.model_dump(),
            "tags": req.tags,
            "note": req.note,
            "image_path": image_path,
            "confidence": req.detect_confidence,
            "embedding": emb.tolist(),
            "extra": {
                "bbox_xyxy": req.bbox_xyxy,
            },
        }
        self.store.insert_memory(payload)
        return {"ok": True, "id": memory_id}

    def upsert_place(self, req: PlaceMemoryUpsertRequest) -> dict[str, Any]:
        ts = req.timestamp or time.time()
        memory_id = f"plc_{uuid.uuid4().hex[:12]}"
        image_path: Optional[str] = None
        has_reference_image = bool(req.image and req.image.strip())
        if has_reference_image:
            try:
                image = decode_image_input(req.image)
            except Exception as exc:
                raise PlaceImageDecodeError("invalid reference image") from exc

            try:
                image_path = self._save_image(image, memory_id)
            except Exception as exc:
                try:
                    (settings.image_dir / f"{memory_id}.jpg").unlink(missing_ok=True)
                except OSError:
                    pass
                raise PlaceImageSaveError("failed to save reference image") from exc

        try:
            image_sha256 = self._sha256_file(Path(image_path)) if image_path else None
        except OSError as exc:
            if image_path:
                try:
                    Path(image_path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise PlaceImageSaveError("failed to hash saved reference image") from exc
        visual_index = {
            "status": "not_indexed",
            "image_id": memory_id,
            "image_sha256": image_sha256,
            "backend": None,
            "version": None,
            "updated_at": None,
            "error": None,
        }

        resolved_image_captured_at = (
            req.image_captured_at if req.image_captured_at is not None else ts
        ) if has_reference_image else None
        payload = {
            "id": memory_id,
            "memory_type": "place",
            "name": req.place_name,
            "robot_id": req.robot_id,
            "robot_type": req.robot_type,
            "source": "human_label",
            "timestamp": ts,
            "timestamp_ns": int(ts * 1e9),
            **self._pose_to_cols(req.place_pose),
            "robot_pose": None,
            "tags": req.alias,
            "note": req.note,
            "image_path": image_path,
            "confidence": 1.0,
            "embedding": text_embedding(req.place_name, settings.embedding_dim).tolist(),
            "extra": {
                "task_description": req.task_description,
                "image_captured_at": resolved_image_captured_at,
                "visual_index": visual_index,
            },
        }
        try:
            self.store.insert_memory(payload)
        except Exception:
            if image_path:
                Path(image_path).unlink(missing_ok=True)
            raise
        return {
            "ok": True,
            "id": memory_id,
            "place_id": memory_id,
            "image_id": memory_id,
            "has_reference_image": has_reference_image,
            "image_path": image_path,
            "image_sha256": image_sha256,
            "visual_index_status": "not_indexed",
            "visual_index": visual_index,
        }

    def ingest_semantic_frame(self, req: SemanticFrameIngestRequest) -> dict[str, Any]:
        ts = req.timestamp or time.time()
        memory_id = f"sem_{uuid.uuid4().hex[:12]}"
        image = decode_image_input(req.image)
        image_path = self._save_image(image, memory_id)
        emb = image_embedding(image, settings.embedding_dim)

        payload = {
            "id": memory_id,
            "memory_type": "semantic_frame",
            "name": req.note or "semantic_frame",
            "robot_id": req.robot_id,
            "robot_type": req.robot_type,
            "source": req.source,
            "timestamp": ts,
            "timestamp_ns": int(ts * 1e9),
            **self._pose_to_cols(req.robot_pose),
            "robot_pose": req.robot_pose.model_dump(),
            "tags": req.tags,
            "note": req.note,
            "image_path": image_path,
            "confidence": 1.0,
            "embedding": emb.tolist(),
            "extra": {"task_id": req.task_id or ""},
        }
        self.store.insert_memory(payload)
        return {"ok": True, "id": memory_id, "image_path": image_path}

    def ingest_keyframe_batch(self, req: KeyframeBatchIngestRequest) -> dict[str, Any]:
        success = 0
        failed = 0
        for item in req.items:
            try:
                ts = item.timestamp or time.time()
                memory_id = f"kf_{uuid.uuid4().hex[:12]}"
                image = decode_image_input(item.image)
                image_path = self._save_image(image, memory_id)
                emb = image_embedding(image, settings.embedding_dim)

                payload = {
                    "id": memory_id,
                    "memory_type": "keyframe",
                    "name": f"keyframe_{item.camera_source}",
                    "robot_id": item.robot_id,
                    "robot_type": item.robot_type,
                    "source": item.camera_source,
                    "timestamp": ts,
                    "timestamp_ns": item.timestamp_ns or int(ts * 1e9),
                    **self._pose_to_cols(item.pose),
                    "robot_pose": None,
                    "tags": [],
                    "note": item.note,
                    "image_path": image_path,
                    "confidence": max(0.0, min(1.0, item.score)),
                    "embedding": emb.tolist(),
                    "extra": {"rank": item.rank, "task_id": req.task_id},
                }
                self.store.insert_memory(payload)
                success += 1
            except Exception:
                failed += 1
        return {"ok": True, "task_id": req.task_id, "success": success, "failed": failed}

    def _row_to_result(self, row: dict[str, Any], score: Optional[float] = None) -> MemoryResult:
        extra = self._safe_json_object(row.get("extra_json"))
        robot_pose_data = self._safe_json_object(row.get("robot_pose_json"))
        target_pose = Pose(
            x=float(row["x"]),
            y=float(row["y"]),
            z=float(row["z"]),
            roll=float(row["roll"]),
            pitch=float(row["pitch"]),
            yaw=float(row["yaw"]),
            qx=row.get("qx"),
            qy=row.get("qy"),
            qz=row.get("qz"),
            qw=row.get("qw"),
            frame_id="map",
        )
        robot_pose = Pose(**robot_pose_data) if robot_pose_data else None
        final_conf = float(row.get("confidence", 1.0))
        if score is not None:
            final_conf = max(0.0, min(1.0, (final_conf + score) / 2.0))

        result_data: dict[str, Any] = {
            "id": row["id"],
            "memory_type": row["memory_type"],
            "name": row["name"],
            "robot_id": row["robot_id"],
            "robot_type": row["robot_type"],
            "target_pose": target_pose,
            "robot_pose": robot_pose,
            "source": row["source"],
            "timestamp": float(row["timestamp"]),
            "confidence": final_conf,
            "evidence": {
                "image_path": row.get("image_path"),
                "note": row.get("note") or "",
                "extra": extra,
            },
        }
        if row["memory_type"] == "place":
            image_sha256 = self._image_sha256_for_row(row)
            visual_index = self._visual_index_for_row(row, extra, image_sha256)
            image_url = f"/memory/place/{row['id']}/image" if image_sha256 else None
            result_data.update(
                {
                    "place_id": row["id"],
                    "image_id": visual_index["image_id"],
                    "image_sha256": image_sha256,
                    "image_url": image_url,
                    "visual_index": visual_index,
                }
            )
            result_data["evidence"].update(
                {
                    "image_id": visual_index["image_id"],
                    "image_sha256": image_sha256,
                    "image_url": image_url,
                }
            )

        return MemoryResult(**result_data)

    def get_place(self, place_id: str) -> dict[str, Any]:
        row = self.store.get_memory_by_id(place_id)
        if row is None or row.get("memory_type") != "place":
            raise PlaceNotFoundError("place not found")

        result = self._row_to_result(row).model_dump()
        extra = self._safe_json_object(row.get("extra_json"))
        try:
            aliases = json.loads(row.get("tags_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            aliases = []
        result["aliases"] = aliases if isinstance(aliases, list) else []
        result["task_description"] = extra.get("task_description", "")
        return result

    def get_place_image(self, place_id: str) -> tuple[Path, str]:
        row = self.store.get_memory_by_id(place_id)
        if row is None or row.get("memory_type") != "place":
            raise PlaceNotFoundError("place not found")

        path = self._resolve_image_path(row.get("image_path"))
        if path is None or not path.is_file():
            raise PlaceImageNotFoundError("place reference image not found")
        try:
            return path, self._sha256_file(path)
        except OSError as exc:
            raise PlaceImageNotFoundError("place reference image not found") from exc

    def update_place_visual_index(
        self,
        place_id: str,
        req: VisualIndexUpdateRequest,
    ) -> dict[str, Any]:
        row = self.store.get_memory_by_id(place_id)
        if row is None or row.get("memory_type") != "place":
            raise PlaceNotFoundError("place not found")

        current_sha256 = self._image_sha256_for_row(row)
        if (
            req.image_sha256 is not None
            and current_sha256 is not None
            and req.image_sha256 != current_sha256
        ):
            raise PlaceImageHashConflictError(
                "image_sha256 does not match the place reference image"
            )

        visual_index = {
            "status": req.status,
            "image_id": req.image_id or place_id,
            "image_sha256": req.image_sha256 or current_sha256,
            "backend": req.backend,
            "version": req.version,
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "error": req.error if req.status == "failed" else None,
        }
        if not self.store.update_memory_extra_key(place_id, "visual_index", visual_index):
            raise PlaceNotFoundError("place not found")
        return {"place_id": place_id, "visual_index": visual_index}

    def query_by_name(self, memory_type: str, name: str, n_results: int, robot_id: Optional[str]) -> list[MemoryResult]:
        rows = self.store.query_memories(memory_type=memory_type, name=name, robot_id=robot_id, limit=n_results)
        return [self._row_to_result(row) for row in rows]

    def query_by_position(
        self,
        x: float,
        y: float,
        radius: float,
        n_results: int,
        memory_type: Optional[str] = None,
    ) -> list[MemoryResult]:
        rows = self.store.all_memories(memory_type=memory_type)
        hits: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            dx = float(row["x"]) - x
            dy = float(row["y"]) - y
            dist = math.hypot(dx, dy)
            if dist <= radius:
                hits.append((dist, row))

        hits.sort(key=lambda x: x[0])
        results = []
        for dist, row in hits[: max(1, n_results)]:
            score = 1.0 / (1.0 + dist)
            results.append(self._row_to_result(row, score=score))
        return results

    def semantic_text_query(self, text: str, n_results: int, memory_type: Optional[str]) -> list[MemoryResult]:
        q = text_embedding(text, settings.embedding_dim)
        rows = self.store.all_memories(memory_type=memory_type)
        scored: list[tuple[float, dict[str, Any]]] = []

        for row in rows:
            emb_raw = json.loads(row.get("embedding_json") or "[]")
            if not emb_raw:
                continue
            emb = np.array(emb_raw, dtype=np.float32)
            score = cosine_similarity(q, emb)
            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._row_to_result(row, score=score) for score, row in scored[: max(1, n_results)]]

    def unified_query(self, req: UnifiedQuery) -> list[MemoryResult]:
        if req.object_name:
            return self.query_by_name("object", req.object_name, req.n_results, req.robot_id)
        if req.place_name:
            return self.query_by_name("place", req.place_name, req.n_results, req.robot_id)
        if req.x is not None and req.y is not None:
            return self.query_by_position(req.x, req.y, req.radius, req.n_results, req.memory_type)
        if req.text:
            return self.semantic_text_query(req.text, req.n_results, req.memory_type)
        rows = self.store.query_memories(memory_type=req.memory_type, robot_id=req.robot_id, limit=req.n_results)
        return [self._row_to_result(row) for row in rows]
