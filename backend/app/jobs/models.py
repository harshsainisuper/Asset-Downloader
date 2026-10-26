from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..media.models import VideoInfo

TaskKind = Literal["video", "audio", "thumbnail", "transcript"]
TaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
TASK_ORDER: tuple[TaskKind, ...] = ("video", "audio", "thumbnail", "transcript")
FINAL_STATES = ("completed", "failed", "cancelled")


@dataclass
class FileArtifact:
    id: str
    task: str  # a TaskKind or "zip"
    path: Path
    download_name: str
    mime: str
    size: int
    label: str
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class TaskState:
    kind: TaskKind
    options: Any
    status: TaskStatus = "queued"
    progress: float = 0.0
    stage: str = "Queued"
    error_code: str | None = None
    error_message: str | None = None
    notes: list[str] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    runner: asyncio.Task | None = None

    @property
    def done(self) -> bool:
        return self.status in FINAL_STATES

    def set_progress(self, value: float, stage: str | None = None) -> None:
        # Progress only moves forward; callbacks may arrive from worker threads.
        value = max(0.0, min(1.0, float(value)))
        if value > self.progress:
            self.progress = value
        if stage:
            self.stage = stage


@dataclass
class Job:
    id: str
    token_hash: str
    client: str
    info: VideoInfo
    dir: Path
    tasks: dict[TaskKind, TaskState]
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    files: dict[str, FileArtifact] = field(default_factory=dict)
    runner: asyncio.Task | None = None
    sources: Any = None  # SourceCache, set by the manager
    zip_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiting_for_slot: bool = True

    @property
    def status(self) -> str:
        states = [t.status for t in self.tasks.values()]
        if not all(s in FINAL_STATES for s in states):
            return "running" if any(s == "running" for s in states) or not self.waiting_for_slot else "queued"
        if all(s == "completed" for s in states):
            return "completed"
        if all(s == "cancelled" for s in states):
            return "cancelled"
        if any(s == "completed" for s in states):
            return "partial"
        return "failed"

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")
