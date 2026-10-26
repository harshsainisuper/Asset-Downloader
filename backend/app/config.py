"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

import json
import secrets
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Server -------------------------------------------------------------
    app_env: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    # Used to sign download URLs. If empty, a random key is generated per process
    # (download links then stop working after a restart, which is acceptable
    # because jobs are kept in memory anyway).
    secret_key: str = ""
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    # Regex for extra allowed origins (only when the UI is on another origin), e.g.
    # ^https://ui(-[a-z0-9-]+)?\.example\.com$
    cors_origin_regex: str = ""
    # Serve the built frontend (frontend/dist) from the API server.
    serve_frontend: bool = True
    frontend_dist: Path = PROJECT_DIR / "frontend" / "dist"
    # Trust X-Forwarded-For for client IPs. Only enable behind exactly one trusted reverse
    # proxy (Render, Railway, Fly.io, nginx...): the entry appended by that proxy is used.
    trust_proxy_headers: bool = False

    # --- Storage & retention -------------------------------------------------
    temp_dir: Path = BACKEND_DIR / "tmp"
    retention_minutes: int = Field(default=60, ge=1)
    cleanup_interval_seconds: int = Field(default=120, ge=10)
    download_link_ttl_minutes: int = Field(default=60, ge=1)

    # --- Limits ---------------------------------------------------------------
    max_request_body_bytes: int = 64 * 1024
    max_concurrent_jobs: int = Field(default=2, ge=1)
    max_active_jobs_per_client: int = Field(default=3, ge=1)
    max_concurrent_ffmpeg: int = Field(default=2, ge=1)
    info_rate_limit_per_minute: int = Field(default=30, ge=1)
    job_rate_limit_per_minute: int = Field(default=10, ge=1)
    info_timeout_seconds: int = Field(default=45, ge=5)
    job_timeout_seconds: int = Field(default=3 * 3600, ge=60)
    max_duration_seconds: int = Field(default=4 * 3600, ge=1)
    max_filesize_mb: int = Field(default=8192, ge=1)
    info_cache_seconds: int = Field(default=600, ge=0)

    # --- External tools -------------------------------------------------------
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    # x264 preset used only when a H.264 re-encode is required.
    x264_preset: str = "veryfast"
    x264_crf: int = Field(default=20, ge=0, le=51)

    # --- yt-dlp provider ----------------------------------------------------------
    # Comma separated JS runtimes yt-dlp may use to solve YouTube challenges.
    ytdlp_js_runtimes: str = "deno,node"
    ytdlp_socket_timeout: int = Field(default=20, ge=1)

    # --- Speech-to-text fallback ------------------------------------------------
    stt_provider: Literal["none", "openai_compatible", "faster_whisper"] = "none"
    stt_api_base_url: str = "https://api.openai.com/v1"
    stt_api_key: str = ""
    stt_model: str = "whisper-1"
    stt_timeout_seconds: int = Field(default=600, ge=10)
    stt_chunk_seconds: int = Field(default=600, ge=30)
    stt_local_model: str = "small"
    stt_local_device: str = "auto"
    stt_local_compute_type: str = "default"
    stt_max_duration_seconds: int = Field(default=2 * 3600, ge=60)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @field_validator("temp_dir", "frontend_dist", mode="after")
    @classmethod
    def _resolve_relative(cls, value: Path) -> Path:
        # Relative paths are relative to the project root, not the process CWD.
        return value if value.is_absolute() else (PROJECT_DIR / value).resolve()

    @property
    def signing_key(self) -> bytes:
        return _signing_key(self.secret_key)

    @property
    def ffmpeg_binary(self) -> str | None:
        return self.ffmpeg_path or shutil.which("ffmpeg")

    @property
    def ffprobe_binary(self) -> str | None:
        return self.ffprobe_path or shutil.which("ffprobe")

    @property
    def max_filesize_bytes(self) -> int:
        return self.max_filesize_mb * 1024 * 1024

    @property
    def stt_enabled(self) -> bool:
        if self.stt_provider == "openai_compatible":
            return bool(self.stt_api_base_url and self.stt_model)
        return self.stt_provider == "faster_whisper"


@lru_cache(maxsize=8)
def _signing_key(configured: str) -> bytes:
    if configured:
        return configured.encode("utf-8")
    return secrets.token_bytes(32)


@lru_cache
def get_settings() -> Settings:
    return Settings()
