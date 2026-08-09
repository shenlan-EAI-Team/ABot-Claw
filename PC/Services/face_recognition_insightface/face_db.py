from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

import numpy as np


def normalize_embedding(embedding: np.ndarray) -> np.ndarray:
    vector = np.asarray(embedding, dtype=np.float32)
    norm = np.linalg.norm(vector)
    if norm == 0:
        raise ValueError("Embedding norm is zero.")
    return vector / norm


def load_database(db_path: Path) -> Dict[str, np.ndarray]:
    if not db_path.exists():
        return {}

    payload = json.loads(db_path.read_text(encoding="utf-8"))
    database: Dict[str, np.ndarray] = {}
    for item in payload.get("people", []):
        name = item.get("name")
        embedding = item.get("embedding")
        if not name or embedding is None:
            continue
        database[name] = normalize_embedding(np.asarray(embedding, dtype=np.float32))
    return database


def save_database(db_path: Path, database: Dict[str, np.ndarray]) -> None:
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


def match_embedding(
    query_embedding: np.ndarray,
    database: Dict[str, np.ndarray],
    threshold: float,
) -> Tuple[str, float]:
    if not database:
        return "Unknown", 0.0

    query = normalize_embedding(query_embedding)
    best_name = "Unknown"
    best_score = -1.0

    for name, known_embedding in database.items():
        score = float(np.dot(query, normalize_embedding(known_embedding)))
        if score > best_score:
            best_name = name
            best_score = score

    if best_score < threshold:
        return "Unknown", best_score
    return best_name, best_score
