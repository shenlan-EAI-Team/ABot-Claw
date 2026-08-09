from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import VPRServiceError
from app.image_io import ImageLoader
from app.services.indexing_service import IndexingService
from tests.conftest import MockDescriptor, image_bytes


def loaded_image(stack: dict, color: tuple[int, int, int]):
    return stack["image_loader"].load_upload(image_bytes(color), "image/png")


def create_red(stack: dict):
    return stack["indexing"].create_from_loaded(
        place_id="place-red",
        image_id="image-red",
        image_url=None,
        loaded=loaded_image(stack, (255, 0, 0)),
        expected_sha256=None,
    )


def test_create_and_idempotent_request_reuses_embedding(service_stack: dict) -> None:
    first = create_red(service_stack)
    calls = service_stack["descriptor"].encode_calls
    second = create_red(service_stack)
    assert first.created is True
    assert first.index_size == 1
    assert second.created is False
    assert service_stack["descriptor"].encode_calls == calls


def test_same_image_id_different_hash_conflicts(service_stack: dict) -> None:
    create_red(service_stack)
    with pytest.raises(VPRServiceError) as caught:
        service_stack["indexing"].create_from_loaded(
            place_id="place-red",
            image_id="image-red",
            image_url=None,
            loaded=loaded_image(service_stack, (0, 255, 0)),
            expected_sha256=None,
        )
    assert caught.value.code == "IMAGE_ID_CONFLICT"


def test_supplied_hash_is_verified(service_stack: dict) -> None:
    with pytest.raises(VPRServiceError) as caught:
        service_stack["indexing"].create_from_loaded(
            place_id="place-red",
            image_id="image-red",
            image_url=None,
            loaded=loaded_image(service_stack, (255, 0, 0)),
            expected_sha256="0" * 64,
        )
    assert caught.value.code == "IMAGE_HASH_MISMATCH"


def test_update_and_delete_rebuild_index(service_stack: dict) -> None:
    create_red(service_stack)
    updated = service_stack["indexing"].update_from_loaded(
        image_id="image-red",
        place_id="place-green",
        image_url=None,
        loaded=loaded_image(service_stack, (0, 255, 0)),
        expected_sha256=None,
    )
    assert updated.place_id == "place-green"
    assert updated.index_size == 1
    service_stack["indexing"].delete("image-red")
    assert service_stack["indexing"].get_snapshot().index.size == 0
    assert service_stack["repository"].count_active() == 0


def test_rebuild_reuses_valid_embedding_cache(service_stack: dict) -> None:
    create_red(service_stack)
    calls = service_stack["descriptor"].encode_calls
    result = service_stack["indexing"].rebuild()
    assert result.index_size == 1
    assert service_stack["descriptor"].encode_calls == calls


def test_model_version_change_invalidates_embedding_cache(service_stack: dict) -> None:
    create_red(service_stack)
    descriptor = MockDescriptor(version="mock_v2")
    descriptor.load()
    replacement = IndexingService(
        service_stack["settings"],
        descriptor,
        service_stack["repository"],
        service_stack["image_loader"],
    )
    replacement.initialize()
    current = service_stack["repository"].get_by_image_id("image-red")
    assert current is not None
    assert current.descriptor_version == "mock_v2"
    assert descriptor.encode_calls == 1
    assert replacement.get_snapshot().index.size == 1


def test_startup_restores_persisted_index_and_mapping_without_reencoding(
    service_stack: dict,
) -> None:
    create_red(service_stack)
    service_stack["indexing"].create_from_loaded(
        place_id="place-green",
        image_id="image-green",
        image_url=None,
        loaded=loaded_image(service_stack, (0, 255, 0)),
        expected_sha256=None,
    )
    descriptor = MockDescriptor()
    descriptor.load()
    restored = IndexingService(
        service_stack["settings"],
        descriptor,
        service_stack["repository"],
        ImageLoader(service_stack["settings"]),
    )
    restored.initialize()
    snapshot = restored.get_snapshot()
    assert snapshot.index.size == 2
    assert [entry.image_id for entry in snapshot.entries] == ["image-red", "image-green"]
    assert descriptor.encode_calls == 0


def test_failed_rebuild_retains_old_snapshot(service_stack: dict) -> None:
    create_red(service_stack)
    old_snapshot = service_stack["indexing"].get_snapshot()
    entry = service_stack["repository"].get_by_image_id("image-red")
    assert entry is not None
    Path(entry.embedding_cache_path).unlink()
    Path(entry.image_cache_path).unlink()
    service_stack["descriptor"].fail = True
    with pytest.raises(VPRServiceError) as caught:
        service_stack["indexing"].rebuild()
    assert caught.value.code == "INDEX_REBUILD_FAILED"
    assert service_stack["indexing"].get_snapshot() is old_snapshot
    assert service_stack["repository"].count_active() == 1


def test_failed_update_retains_old_record_and_snapshot(
    service_stack: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_red(service_stack)
    old_snapshot = service_stack["indexing"].get_snapshot()
    old_entry = service_stack["repository"].get_by_image_id("image-red")
    assert old_entry is not None

    def fail_stage(index):
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(service_stack["indexing"], "_stage_index", fail_stage)
    with pytest.raises(VPRServiceError) as caught:
        service_stack["indexing"].update_from_loaded(
            image_id="image-red",
            place_id="place-green",
            image_url=None,
            loaded=loaded_image(service_stack, (0, 255, 0)),
            expected_sha256=None,
        )
    current = service_stack["repository"].get_by_image_id("image-red")
    assert caught.value.code == "INDEX_REBUILD_FAILED"
    assert current == old_entry
    assert service_stack["indexing"].get_snapshot() is old_snapshot


def test_create_from_allowed_local_source(service_stack: dict, tmp_path: Path) -> None:
    source = tmp_path / "reference.png"
    source.write_bytes(image_bytes((0, 0, 255)))
    result = service_stack["indexing"].create_from_source(
        place_id="place-blue",
        image_id="image-blue",
        image_url=str(source),
        expected_sha256=None,
    )
    assert result.index_size == 1
