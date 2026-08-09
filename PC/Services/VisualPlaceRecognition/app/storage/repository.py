"""Parameterized SQL repository for visual index entries and service state."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from app.storage.database import SQLiteDatabase
from app.storage.models import VisualIndexEntry


ENTRY_COLUMNS = (
    "place_id",
    "image_id",
    "image_url",
    "image_sha256",
    "image_cache_path",
    "embedding_cache_path",
    "descriptor_backend",
    "descriptor_version",
    "descriptor_dimension",
    "normalized",
    "active",
    "created_at",
    "updated_at",
    "indexed_at",
    "last_error",
)


class VisualIndexRepository:
    """Persist only stable visual IDs and index-derived metadata."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    @contextmanager
    def _connection(
        self, connection: sqlite3.Connection | None = None
    ) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
            return
        own = self.database.connect()
        try:
            yield own
            own.commit()
        finally:
            own.close()

    def create(
        self,
        entry: VisualIndexEntry,
        connection: sqlite3.Connection | None = None,
    ) -> VisualIndexEntry:
        placeholders = ", ".join("?" for _ in ENTRY_COLUMNS)
        values = [self._db_value(getattr(entry, column)) for column in ENTRY_COLUMNS]
        with self._connection(connection) as conn:
            cursor = conn.execute(
                f"INSERT INTO visual_index_entries ({', '.join(ENTRY_COLUMNS)}) VALUES ({placeholders})",
                values,
            )
            return entry.with_id(int(cursor.lastrowid))

    def get_by_image_id(
        self,
        image_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> VisualIndexEntry | None:
        with self._connection(connection) as conn:
            row = conn.execute(
                "SELECT * FROM visual_index_entries WHERE image_id = ?",
                (image_id,),
            ).fetchone()
        return VisualIndexEntry.from_row(row) if row else None

    def get_by_place_id(self, place_id: str) -> list[VisualIndexEntry]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM visual_index_entries WHERE place_id = ? ORDER BY id",
                (place_id,),
            ).fetchall()
        return [VisualIndexEntry.from_row(row) for row in rows]

    def list_active(
        self,
        connection: sqlite3.Connection | None = None,
    ) -> list[VisualIndexEntry]:
        with self._connection(connection) as conn:
            rows = conn.execute(
                "SELECT * FROM visual_index_entries WHERE active = 1 ORDER BY id"
            ).fetchall()
        return [VisualIndexEntry.from_row(row) for row in rows]

    def update(
        self,
        image_id: str,
        updates: dict[str, Any],
        connection: sqlite3.Connection | None = None,
    ) -> VisualIndexEntry | None:
        if not updates:
            return self.get_by_image_id(image_id, connection)
        unknown = set(updates) - set(ENTRY_COLUMNS)
        if unknown:
            raise ValueError(f"Unsupported entry fields: {sorted(unknown)}")
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = [self._db_value(value) for value in updates.values()]
        with self._connection(connection) as conn:
            conn.execute(
                f"UPDATE visual_index_entries SET {assignments} WHERE image_id = ?",
                (*values, image_id),
            )
            row = conn.execute(
                "SELECT * FROM visual_index_entries WHERE image_id = ?",
                (image_id,),
            ).fetchone()
        return VisualIndexEntry.from_row(row) if row else None

    def delete(
        self,
        image_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        with self._connection(connection) as conn:
            cursor = conn.execute(
                "DELETE FROM visual_index_entries WHERE image_id = ?",
                (image_id,),
            )
        return cursor.rowcount > 0

    def set_last_error(self, image_id: str, message: str | None) -> None:
        self.update(image_id, {"last_error": message})

    def count_active(self) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM visual_index_entries WHERE active = 1"
            ).fetchone()
        return int(row["count"])

    def get_state(
        self,
        key: str,
        connection: sqlite3.Connection | None = None,
    ) -> str | None:
        with self._connection(connection) as conn:
            row = conn.execute("SELECT value FROM service_state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_state(
        self,
        key: str,
        value: str,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        with self._connection(connection) as conn:
            conn.execute(
                """
                INSERT INTO service_state(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    @staticmethod
    def _db_value(value: Any) -> Any:
        if isinstance(value, bool):
            return int(value)
        return value

