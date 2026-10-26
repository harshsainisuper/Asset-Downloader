from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TranscriptFormat = Literal["txt", "srt", "vtt"]
TranscriptOrigin = Literal["manual_captions", "auto_captions", "ai_transcript"]

ORIGIN_LABELS: dict[str, str] = {
    "manual_captions": "Creator-provided captions (retrieved from YouTube)",
    "auto_captions": "YouTube automatic captions (retrieved from YouTube)",
    "ai_transcript": "AI-generated transcript (speech-to-text; may contain errors)",
}
ORIGIN_SHORT: dict[str, str] = {
    "manual_captions": "captions",
    "auto_captions": "auto-captions",
    "ai_transcript": "AI transcript",
}


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    segments: list[Segment]
    origin: TranscriptOrigin
    language: str | None
    language_name: str | None = None
    model: str | None = None  # STT model for AI transcripts

    @property
    def description(self) -> str:
        label = ORIGIN_LABELS[self.origin]
        details = []
        if self.language_name or self.language:
            details.append(f"language: {self.language_name or self.language}")
        if self.model:
            details.append(f"model: {self.model}")
        return f"{label}" + (f" [{', '.join(details)}]" if details else "")
