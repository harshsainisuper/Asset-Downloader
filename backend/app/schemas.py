"""Request schemas. Unknown fields are rejected so clients cannot smuggle options."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .url_validation import MAX_URL_LENGTH

QualityValue = Literal["best", 2160, 1440, 1080, 720, 480, 360]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoOptions(_Strict):
    quality: QualityValue = "best"
    format: Literal["mp4", "webm"] = "mp4"
    h264_compat: bool = False

    @field_validator("quality", mode="before")
    @classmethod
    def _coerce_quality(cls, value: object) -> object:
        # Accept "1080", "1080p" or 1080.
        if isinstance(value, str) and value != "best":
            digits = value.lower().removesuffix("p")
            if digits.isdigit():
                return int(digits)
        return value


class AudioOptions(_Strict):
    format: Literal["mp3", "m4a", "wav"] = "mp3"
    mp3_bitrate: Literal[320, 256, 192, 128] = 320


class ThumbnailOptions(_Strict):
    format: Literal["jpg", "original"] = "jpg"


class TranscriptOptions(_Strict):
    track_id: str | None = Field(default=None, max_length=80, pattern=r"^(auto|(manual|auto):[A-Za-z0-9_\-]{1,40})$")
    formats: list[Literal["txt", "srt", "vtt"]] = Field(default_factory=lambda: ["srt"], min_length=1, max_length=3)
    ai_fallback: bool = False

    @field_validator("formats")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class Outputs(_Strict):
    video: VideoOptions | None = None
    audio: AudioOptions | None = None
    thumbnail: ThumbnailOptions | None = None
    transcript: TranscriptOptions | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Outputs:
        if not self.selected():
            raise ValueError("Select at least one output to process.")
        return self

    def selected(self) -> list[str]:
        return [k for k in ("video", "audio", "thumbnail", "transcript") if getattr(self, k) is not None]


class InfoRequest(_Strict):
    url: str = Field(max_length=MAX_URL_LENGTH)


class JobCreateRequest(_Strict):
    url: str = Field(max_length=MAX_URL_LENGTH)
    outputs: Outputs


class CancelRequest(_Strict):
    task: Literal["video", "audio", "thumbnail", "transcript"] | None = None


class ZipRequest(_Strict):
    file_ids: list[str] | None = Field(default=None, max_length=50)
