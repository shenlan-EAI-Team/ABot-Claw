"""Internal typed representation of one indexed reference image."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class VisualIndexEntry:
    id: int | None
    place_id: str
    image_id: str
    image_url: str
    image_sha256: str
    image_cache_path: str
    embedding_cache_path: str
    descriptor_backend: str
    descriptor_version: str
    descriptor_dimension: int
    normalized: bool
    active: bool
    created_at: str
    updated_at: str
    indexed_at: str | None
    last_error: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "VisualIndexEntry":
        values = dict(row)
        values["normalized"] = bool(values["normalized"])
        values["active"] = bool(values["active"])
        return cls(**values)

    def with_id(self, entry_id: int) -> "VisualIndexEntry":
        return replace(self, id=entry_id)

