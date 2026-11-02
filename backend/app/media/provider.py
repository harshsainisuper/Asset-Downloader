"""Abstract media provider.

All YouTube and Instagram access goes through this interface so the yt-dlp based implementation
can be swapped for an authorized/official provider without touching the job,
format-planning, FFmpeg or transcript code.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from ..url_validation import VideoRef
from .models import CaptionTrack, VideoInfo

# Called with (downloaded_bytes, total_bytes_or_None)
ByteProgress = Callable[[int, int | None], None]


class MediaProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def fetch_info(self, ref: VideoRef) -> VideoInfo:
        """Return normalized metadata. Raises AppError for inaccessible videos."""

    @abstractmethod
    async def download_format(
        self,
        info: VideoInfo,
        format_id: str,
        dest_dir: Path,
        on_progress: ByteProgress,
        cancel: threading.Event,
    ) -> Path:
        """Download a single elementary stream/format and return its path."""

    @abstractmethod
    async def download_captions(
        self,
        info: VideoInfo,
        track: CaptionTrack,
        dest_dir: Path,
        cancel: threading.Event,
    ) -> Path:
        """Download a caption track (json3 or vtt preferred) and return its path."""

    def diagnostics(self) -> dict[str, object]:
        return {"name": self.name}
