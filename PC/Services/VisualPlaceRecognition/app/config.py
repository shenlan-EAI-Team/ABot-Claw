"""Environment-backed configuration for the VPR service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = BASE_DIR / "data"


def _env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    """All runtime settings, with paths independent of the working directory."""

    service_name: str
    service_version: str
    host: str
    port: int
    device: str
    data_dir: Path
    index_path: Path
    database_path: Path
    cache_dir: Path
    descriptor_backend: str
    descriptor_version: str
    salad_repo: str
    salad_model: str
    salad_image_height: int
    salad_image_width: int
    top_k: int
    unknown_threshold: float
    ambiguous_margin: float
    request_timeout_seconds: float
    max_image_bytes: int
    log_level: str
    allowed_url_hosts: tuple[str, ...]
    local_image_roots: tuple[Path, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from the current process environment."""
        data_dir = Path(os.getenv("VPR_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser().resolve()
        cache_dir = Path(os.getenv("VPR_CACHE_DIR", str(data_dir / "cache"))).expanduser().resolve()
        local_roots = tuple(
            Path(item).expanduser().resolve()
            for item in _split_csv(os.getenv("VPR_LOCAL_IMAGE_ROOTS"))
        )
        settings = cls(
            service_name="VisualPlaceRecognition",
            service_version="0.1.0",
            host=os.getenv("VPR_HOST", "0.0.0.0"),
            port=_env_int("VPR_PORT", 8030),
            device=os.getenv("VPR_DEVICE", "auto").strip().lower(),
            data_dir=data_dir,
            index_path=Path(
                os.getenv("VPR_INDEX_PATH", str(data_dir / "index.faiss"))
            ).expanduser().resolve(),
            database_path=Path(
                os.getenv("VPR_DATABASE_PATH", str(data_dir / "index.sqlite3"))
            ).expanduser().resolve(),
            cache_dir=cache_dir,
            descriptor_backend=os.getenv("VPR_DESCRIPTOR_BACKEND", "salad").strip().lower(),
            descriptor_version=os.getenv("VPR_DESCRIPTOR_VERSION", "salad_v1").strip(),
            salad_repo=os.getenv("VPR_SALAD_REPO", "serizba/salad").strip(),
            salad_model=os.getenv("VPR_SALAD_MODEL", "dinov2_salad").strip(),
            salad_image_height=_env_int("VPR_SALAD_IMAGE_HEIGHT", 322),
            salad_image_width=_env_int("VPR_SALAD_IMAGE_WIDTH", 322),
            top_k=_env_int("VPR_TOP_K", 2),
            unknown_threshold=_env_float("VPR_UNKNOWN_THRESHOLD", 0.60),
            ambiguous_margin=_env_float("VPR_AMBIGUOUS_MARGIN", 0.08),
            request_timeout_seconds=_env_float("VPR_REQUEST_TIMEOUT_SECONDS", 15.0),
            max_image_bytes=_env_int("VPR_MAX_IMAGE_BYTES", 20 * 1024 * 1024),
            log_level=os.getenv("VPR_LOG_LEVEL", "INFO").upper(),
            allowed_url_hosts=tuple(
                item.lower() for item in _split_csv(os.getenv("VPR_ALLOWED_URL_HOSTS"))
            ),
            local_image_roots=local_roots,
        )
        settings.validate()
        return settings

    @property
    def embedding_cache_dir(self) -> Path:
        return self.cache_dir / "embeddings"

    @property
    def image_cache_dir(self) -> Path:
        return self.cache_dir / "images"

    def validate(self) -> None:
        """Reject unsafe or internally inconsistent configuration."""
        if self.descriptor_backend != "salad":
            raise ValueError("VPR_DESCRIPTOR_BACKEND currently supports only 'salad'")
        if self.device not in {"auto", "cpu", "cuda"} and not self.device.startswith("cuda:"):
            raise ValueError("VPR_DEVICE must be auto, cpu, cuda, or cuda:<index>")
        if not -1.0 <= self.unknown_threshold <= 1.0:
            raise ValueError("VPR_UNKNOWN_THRESHOLD must be between -1 and 1")
        if not 0.0 <= self.ambiguous_margin <= 2.0:
            raise ValueError("VPR_AMBIGUOUS_MARGIN must be between 0 and 2")
        if self.request_timeout_seconds <= 0:
            raise ValueError("VPR_REQUEST_TIMEOUT_SECONDS must be greater than zero")

    def ensure_directories(self) -> None:
        """Create all writable runtime directories."""
        for path in (
            self.data_dir,
            self.index_path.parent,
            self.database_path.parent,
            self.cache_dir,
            self.embedding_cache_dir,
            self.image_cache_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()

