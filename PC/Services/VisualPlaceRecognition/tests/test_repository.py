from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.storage.database import SQLiteDatabase
from app.storage.models import VisualIndexEntry
from app.storage.repository import VisualIndexRepository


def entry(image_id: str = "img-1", place_id: str = "place-1") -> VisualIndexEntry:
    now = datetime.now(timezone.utc).isoformat()
    return VisualIndexEntry(
        id=None,
        place_id=place_id,
        image_id=image_id,
        image_url="http://example.test/image.png",
        image_sha256="a" * 64,
        image_cache_path="cache/image.png",
        embedding_cache_path="cache/embedding.npy",
        descriptor_backend="mock",
        descriptor_version="v1",
        descriptor_dimension=4,
        normalized=True,
        active=True,
        created_at=now,
        updated_at=now,
        indexed_at=now,
        last_error=None,
    )


@pytest.fixture
def repository(tmp_path: Path) -> VisualIndexRepository:
    database = SQLiteDatabase(tmp_path / "isolated.sqlite3")
    database.initialize()
    return VisualIndexRepository(database)


def test_create_query_update_and_delete(repository: VisualIndexRepository) -> None:
    created = repository.create(entry())
    assert created.id is not None
    assert repository.get_by_image_id("img-1") == created
    assert repository.get_by_place_id("place-1") == [created]
    updated = repository.update("img-1", {"place_id": "place-2", "last_error": "x"})
    assert updated is not None
    assert updated.place_id == "place-2"
    assert updated.last_error == "x"
    assert repository.count_active() == 1
    assert repository.delete("img-1") is True
    assert repository.get_by_image_id("img-1") is None


def test_image_id_unique_constraint(repository: VisualIndexRepository) -> None:
    repository.create(entry())
    with pytest.raises(sqlite3.IntegrityError):
        repository.create(entry(place_id="place-2"))


def test_active_list_and_soft_deactivation(repository: VisualIndexRepository) -> None:
    repository.create(entry("img-1"))
    repository.create(entry("img-2"))
    repository.update("img-1", {"active": False})
    assert [item.image_id for item in repository.list_active()] == ["img-2"]


def test_service_state(repository: VisualIndexRepository) -> None:
    assert repository.get_state("index_version") is None
    repository.set_state("index_version", "3")
    repository.set_state("index_version", "4")
    assert repository.get_state("index_version") == "4"


def test_temporary_databases_are_isolated(tmp_path: Path) -> None:
    first_db = SQLiteDatabase(tmp_path / "first.sqlite3")
    second_db = SQLiteDatabase(tmp_path / "second.sqlite3")
    first_db.initialize()
    second_db.initialize()
    first = VisualIndexRepository(first_db)
    second = VisualIndexRepository(second_db)
    first.create(entry())
    assert first.count_active() == 1
    assert second.count_active() == 0

