"""Bounded and validated image loading for URLs, uploads, and allowed paths."""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from app.config import Settings
from app.errors import VPRServiceError


LOGGER = logging.getLogger(__name__)
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}


@dataclass(slots=True)
class LoadedImage:
    """A decoded RGB image together with the exact bytes that were hashed."""

    image: Image.Image
    raw_bytes: bytes
    sha256: str
    image_format: str


class ImageLoader:
    """Load images while enforcing byte, protocol, host, and path restrictions."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def load_source(self, source: str) -> LoadedImage:
        """Load an HTTP(S) URL or a local path under an explicitly allowed root."""
        source = source.strip()
        if not source:
            raise VPRServiceError(400, "INVALID_REQUEST", "image_url must not be empty")
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            return self._load_url(source)
        if parsed.scheme:
            raise VPRServiceError(
                400,
                "INVALID_REQUEST",
                "Only http and https image URLs are allowed",
            )
        return self._load_local_path(Path(source))

    def load_upload(self, raw_bytes: bytes, content_type: str | None = None) -> LoadedImage:
        """Validate an already bounded multipart image body."""
        if content_type and not content_type.lower().startswith("image/"):
            raise VPRServiceError(415, "UNSUPPORTED_IMAGE", "Upload MIME type is not an image")
        return self._decode(raw_bytes)

    def load_cached(self, cache_path: str | Path) -> LoadedImage:
        """Read a service-owned cached image, rejecting path traversal."""
        return self._load_local_path(Path(cache_path), internal_cache_only=True)

    def _load_url(self, url: str) -> LoadedImage:
        self._validate_url(url)
        try:
            timeout = httpx.Timeout(self.settings.request_timeout_seconds)
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", url, headers={"Accept": "image/*"}) as response:
                    response.raise_for_status()
                    self._validate_url(str(response.url))
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > self.settings.max_image_bytes:
                        raise VPRServiceError(413, "IMAGE_TOO_LARGE", "Reference image is too large")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.settings.max_image_bytes:
                            raise VPRServiceError(413, "IMAGE_TOO_LARGE", "Reference image is too large")
        except VPRServiceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.warning("Reference image download failed for host=%s", urlparse(url).hostname)
            raise VPRServiceError(
                400,
                "IMAGE_DOWNLOAD_FAILED",
                "Unable to download reference image",
            ) from exc
        return self._decode(bytes(body))

    def _load_local_path(
        self,
        path: Path,
        *,
        internal_cache_only: bool = False,
    ) -> LoadedImage:
        resolved = path.expanduser().resolve()
        cache_root = self.settings.image_cache_dir.resolve()
        allowed_roots = (cache_root,) if internal_cache_only else (cache_root, *self.settings.local_image_roots)
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            raise VPRServiceError(
                400,
                "INVALID_REQUEST",
                "Local image path is outside the configured allowed roots",
            )
        try:
            if resolved.stat().st_size > self.settings.max_image_bytes:
                raise VPRServiceError(413, "IMAGE_TOO_LARGE", "Reference image is too large")
            raw_bytes = resolved.read_bytes()
        except VPRServiceError:
            raise
        except OSError as exc:
            raise VPRServiceError(
                400,
                "IMAGE_DOWNLOAD_FAILED",
                "Unable to read reference image",
            ) from exc
        return self._decode(raw_bytes)

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise VPRServiceError(400, "INVALID_REQUEST", "Invalid HTTP(S) image URL")
        if parsed.username or parsed.password:
            raise VPRServiceError(400, "INVALID_REQUEST", "Credentials in image URLs are not allowed")
        hostname = parsed.hostname.lower()
        if self.settings.allowed_url_hosts and not any(
            hostname == rule or (rule.startswith("*.") and hostname.endswith(rule[1:]))
            for rule in self.settings.allowed_url_hosts
        ):
            raise VPRServiceError(
                400,
                "INVALID_REQUEST",
                "Image URL host is not in VPR_ALLOWED_URL_HOSTS",
            )

    def _decode(self, raw_bytes: bytes) -> LoadedImage:
        if not raw_bytes:
            raise VPRServiceError(415, "UNSUPPORTED_IMAGE", "Image body is empty")
        if len(raw_bytes) > self.settings.max_image_bytes:
            raise VPRServiceError(413, "IMAGE_TOO_LARGE", "Image is too large")
        try:
            with Image.open(io.BytesIO(raw_bytes)) as probe:
                image_format = (probe.format or "").upper()
                probe.verify()
            if image_format not in SUPPORTED_FORMATS:
                raise VPRServiceError(
                    415,
                    "UNSUPPORTED_IMAGE",
                    f"Unsupported image format: {image_format or 'unknown'}",
                )
            with Image.open(io.BytesIO(raw_bytes)) as decoded:
                decoded.load()
                image = decoded.convert("RGB")
        except VPRServiceError:
            raise
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
            raise VPRServiceError(415, "UNSUPPORTED_IMAGE", "Unable to decode image") from exc
        return LoadedImage(
            image=image,
            raw_bytes=raw_bytes,
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
            image_format=image_format,
        )


async def read_upload_limited(upload: object, max_bytes: int) -> bytes:
    """Read a FastAPI UploadFile incrementally with a hard byte limit."""
    body = bytearray()
    while True:
        chunk = await upload.read(1024 * 1024)  # type: ignore[attr-defined]
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > max_bytes:
            raise VPRServiceError(413, "IMAGE_TOO_LARGE", "Uploaded image is too large")
    return bytes(body)

