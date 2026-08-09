from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from app.main import create_app
from tests.conftest import MockDescriptor, image_bytes


def upload_payload(color: tuple[int, int, int]):
    return {"image": ("query.png", image_bytes(color), "image/png")}


async def create_reference(client: httpx.AsyncClient, place_id: str, image_id: str, color):
    return await client.post(
        "/visual-index/images/upload",
        data={"place_id": place_id, "image_id": image_id},
        files=upload_payload(color),
    )


@pytest.mark.asyncio
async def test_health_and_status(api_context) -> None:
    client, _, _, _ = api_context
    assert (await client.get("/health")).json() == {
        "status": "ok",
        "service": "VisualPlaceRecognition",
    }
    status = await client.get("/visual-index/status")
    assert status.status_code == 200
    assert status.json()["ready"] is True
    assert status.json()["index_size"] == 0


@pytest.mark.asyncio
async def test_upload_create_idempotent_and_conflict(api_context) -> None:
    client, _, descriptor, _ = api_context
    created = await create_reference(client, "place-red", "image-red", (255, 0, 0))
    assert created.status_code == 201
    calls = descriptor.encode_calls
    repeated = await create_reference(client, "place-red", "image-red", (255, 0, 0))
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert descriptor.encode_calls == calls
    conflict = await create_reference(client, "place-red", "image-red", (0, 255, 0))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IMAGE_ID_CONFLICT"


@pytest.mark.asyncio
async def test_search_and_verify(api_context) -> None:
    client, _, _, _ = api_context
    assert (await create_reference(client, "place-red", "image-red", (255, 0, 0))).status_code == 201
    assert (await create_reference(client, "place-green", "image-green", (0, 255, 0))).status_code == 201
    search = await client.post(
        "/visual-index/search",
        data={"top_k": "2"},
        files=upload_payload((250, 5, 0)),
    )
    assert search.status_code == 200
    body = search.json()
    assert body["decision"] == "matched"
    assert body["candidates"][0]["place_id"] == "place-red"
    assert "probability" not in body
    verify = await client.post(
        "/visual-index/verify",
        data={"target_place_id": "place-red"},
        files=upload_payload((250, 5, 0)),
    )
    assert verify.status_code == 200
    assert verify.json()["verified"] is True
    assert verify.json()["target_rank"] == 1


@pytest.mark.asyncio
async def test_empty_index_search_and_verify(api_context) -> None:
    client, _, _, _ = api_context
    search = await client.post("/visual-index/search", files=upload_payload((1, 2, 3)))
    assert search.status_code == 200
    assert search.json()["decision"] == "empty_index"
    verify = await client.post(
        "/visual-index/verify",
        data={"target_place_id": "missing"},
        files=upload_payload((1, 2, 3)),
    )
    assert verify.status_code == 200
    assert verify.json()["decision"] == "empty_index"


@pytest.mark.asyncio
async def test_update_delete_and_rebuild(api_context) -> None:
    client, _, _, _ = api_context
    await create_reference(client, "place-red", "image-red", (255, 0, 0))
    update = await client.put(
        "/visual-index/images/image-red/upload",
        data={"place_id": "place-green"},
        files=upload_payload((0, 255, 0)),
    )
    assert update.status_code == 200
    assert update.json()["place_id"] == "place-green"
    rebuilt = await client.post("/visual-index/rebuild")
    assert rebuilt.status_code == 200
    assert rebuilt.json()["index_size"] == 1
    deleted = await client.delete("/visual-index/images/image-red")
    assert deleted.status_code == 204
    assert (await client.get("/visual-index/status")).json()["index_size"] == 0
    missing = await client.delete("/visual-index/images/image-red")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_json_local_path_create_and_update(api_context, tmp_path) -> None:
    client, _, _, _ = api_context
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(image_bytes((255, 0, 0)))
    second.write_bytes(image_bytes((0, 0, 255)))
    created = await client.post(
        "/visual-index/images",
        json={
            "place_id": "place-one",
            "image_id": "image-one",
            "image_url": str(first),
        },
    )
    assert created.status_code == 201
    updated = await client.put(
        "/visual-index/images/image-one",
        json={"place_id": "place-two", "image_url": str(second)},
    )
    assert updated.status_code == 200
    assert updated.json()["place_id"] == "place-two"


@pytest.mark.asyncio
async def test_json_http_url_create(api_context, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _, _ = api_context
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "images.example.test"
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=image_bytes((0, 0, 255)),
            request=request,
        )

    def mock_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.image_io.httpx.Client", mock_client)
    response = await client.post(
        "/visual-index/images",
        json={
            "place_id": "place-http",
            "image_id": "image-http",
            "image_url": "https://images.example.test/reference.png",
        },
    )
    assert response.status_code == 201
    assert response.json()["image_sha256"]


@pytest.mark.asyncio
async def test_invalid_and_oversized_images(api_context) -> None:
    client, application, descriptor, settings = api_context
    invalid = await client.post(
        "/visual-index/search",
        files={"image": ("bad.jpg", b"not-an-image", "image/jpeg")},
    )
    assert invalid.status_code == 415
    assert invalid.json()["error"]["code"] == "UNSUPPORTED_IMAGE"

    tiny_limit = replace(settings, max_image_bytes=10)
    limited_app = create_app(tiny_limit, MockDescriptor())
    async with limited_app.router.lifespan_context(limited_app):
        transport = httpx.ASGITransport(app=limited_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as limited:
            oversized = await limited.post(
                "/visual-index/search",
                files={"image": ("large.jpg", b"x" * 11, "image/jpeg")},
            )
            assert oversized.status_code == 413
            assert oversized.json()["error"]["code"] == "IMAGE_TOO_LARGE"


@pytest.mark.asyncio
async def test_non_image_mime_rejected(api_context) -> None:
    client, _, _, _ = api_context
    response = await client.post(
        "/visual-index/search",
        files={"image": ("payload.txt", image_bytes((1, 2, 3)), "text/plain")},
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_model_not_ready_is_structured(settings_factory) -> None:
    descriptor = MockDescriptor()
    descriptor.load = lambda: None  # type: ignore[method-assign]
    application = create_app(settings_factory(), descriptor)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/visual-index/search",
                files=upload_payload((1, 2, 3)),
            )
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "MODEL_NOT_READY"
