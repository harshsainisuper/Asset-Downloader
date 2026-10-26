"""Per-job cache of downloaded source streams.

When several outputs need the same stream (e.g. the AAC track for an MP4 video, an
M4A audio file and the AI transcription), it is downloaded once and shared. A
download is cancelled only when every task waiting on it has been cancelled.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..media.models import VideoInfo
from ..media.provider import MediaProvider

Listener = Callable[[float], None]


@dataclass
class _Source:
    task: asyncio.Task
    cancel: threading.Event
    listeners: set[Listener] = field(default_factory=set)
    waiters: int = 0
    fraction: float = 0.0


class SourceCache:
    def __init__(self, provider: MediaProvider, info: VideoInfo, dest_dir: Path):
        self.provider = provider
        self.info = info
        self.dest_dir = dest_dir
        self._sources: dict[str, _Source] = {}
        self.downloads_started = 0  # exposed for tests/diagnostics

    def _start(self, format_id: str) -> _Source:
        cancel = threading.Event()
        holder: dict[str, _Source] = {}

        def on_bytes(done: int, total: int | None) -> None:
            src = holder.get("src")
            if src is None or not total:
                return
            src.fraction = min(1.0, done / total)
            for listener in list(src.listeners):
                listener(src.fraction)

        task = asyncio.create_task(
            self.provider.download_format(self.info, format_id, self.dest_dir, on_bytes, cancel),
            name=f"source:{format_id}",
        )
        # Mark the exception as retrieved even if every waiter has gone away.
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        src = _Source(task=task, cancel=cancel)
        holder["src"] = src
        self._sources[format_id] = src
        self.downloads_started += 1
        return src

    async def get(self, format_id: str, on_progress: Listener | None = None) -> Path:
        src = self._sources.get(format_id)
        if src is None or src.task.cancelled() or src.cancel.is_set():
            src = self._start(format_id)
        if on_progress:
            src.listeners.add(on_progress)
            if src.fraction:
                on_progress(src.fraction)
        src.waiters += 1
        try:
            path = await asyncio.shield(src.task)
            if on_progress:
                on_progress(1.0)
            return path
        finally:
            if on_progress:
                src.listeners.discard(on_progress)
            src.waiters -= 1
            if src.waiters == 0 and not src.task.done():
                src.cancel.set()
                src.task.cancel()

    def cancel_all(self) -> None:
        for src in self._sources.values():
            src.cancel.set()
            if not src.task.done():
                src.task.cancel()
