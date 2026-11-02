"""Provider-independent media metadata types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CaptionKind = Literal["manual", "auto"]


@dataclass
class MediaFormat:
    format_id: str
    ext: str
    vcodec: str | None = None  # None means "no video"
    acodec: str | None = None  # None means "no audio"
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    tbr: float | None = None  # total bitrate, kbit/s
    abr: float | None = None  # audio bitrate, kbit/s
    asr: int | None = None  # audio sample rate
    filesize: int | None = None  # exact or approximate bytes
    format_note: str = ""
    dynamic_range: str | None = None  # "SDR", "HDR10", ...
    language: str | None = None
    language_preference: int = -1  # higher is better; YouTube marks the original track
    protocol: str = "https"
    is_drc: bool = False

    @property
    def has_video(self) -> bool:
        return self.vcodec is not None

    @property
    def has_audio(self) -> bool:
        return self.acodec is not None


@dataclass
class CaptionTrack:
    key: str  # provider language key, e.g. "en", "en-orig", "pt-BR"
    language: str  # base language code, e.g. "en"
    name: str
    kind: CaptionKind
    is_original: bool = False
    formats: list[str] = field(default_factory=list)

    @property
    def track_id(self) -> str:
        return f"{self.kind}:{self.key}"


@dataclass
class Thumbnail:
    url: str
    width: int | None = None
    height: int | None = None


@dataclass
class VideoInfo:
    video_id: str
    title: str
    channel: str | None
    duration: float | None
    is_short: bool
    thumbnail_url: str | None
    thumbnails: list[Thumbnail]
    formats: list[MediaFormat]
    captions: list[CaptionTrack]
    original_language: str | None = None
    live_status: str | None = None
    webpage_url: str = ""
    platform: str = "youtube"
    # Opaque provider payload needed to download without re-extracting.
    raw: Any = None

    def caption_by_id(self, track_id: str) -> CaptionTrack | None:
        return next((c for c in self.captions if c.track_id == track_id), None)

    def format_by_id(self, format_id: str) -> MediaFormat | None:
        return next((f for f in self.formats if f.format_id == format_id), None)
