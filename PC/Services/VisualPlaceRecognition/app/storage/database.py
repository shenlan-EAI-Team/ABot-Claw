"""SQLite connection and transaction management."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SQLiteDatabase:
    """Create short-lived SQLite connections with safe service pragmas."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            connection = self.connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS visual_index_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        place_id TEXT NOT NULL,
                        image_id TEXT NOT NULL UNIQUE,
                        image_url TEXT NOT NULL,
                        image_sha256 TEXT NOT NULL,
                        image_cache_path TEXT NOT NULL,
                        embedding_cache_path TEXT NOT NULL,
                        descriptor_backend TEXT NOT NULL,
                        descriptor_version TEXT NOT NULL,
                        descriptor_dimension INTEGER NOT NULL,
                        normalized INTEGER NOT NULL DEFAULT 1,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        indexed_at TEXT,
                        last_error TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_visual_entries_place
                        ON visual_index_entries(place_id);
                    CREATE INDEX IF NOT EXISTS idx_visual_entries_active
                        ON visual_index_entries(active);
                    CREATE TABLE IF NOT EXISTS service_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialize writes and roll back the complete metadata operation on failure."""
        with self._write_lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

