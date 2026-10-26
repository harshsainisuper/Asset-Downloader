"""In-memory job registry, scheduler, retention cleanup and ZIP packaging.

Jobs live in process memory, so the API must run as a single worker process. Files
live under ``TEMP_DIR/jobs/<job id>`` and are deleted after the retention period;
anything left over from a previous run is removed on startup.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import zipfile
from pathlib import Path

from ..config import Settings
from ..errors import AppError, CancelledByUser
from ..filenames import safe_stem
from ..media.models import VideoInfo
from ..media.provider import MediaProvider
from ..schemas import Outputs
from ..security import hash_token, new_access_token, new_file_id, new_job_id, token_matches
from ..transcripts.stt import SttProvider
from .models import TASK_ORDER, FileArtifact, Job, TaskState
from .processors import PROCESSORS, TaskContext
from .sources import SourceCache

log = logging.getLogger(__name__)

_STORED_EXTS = {".mp4", ".webm", ".mp3", ".m4a", ".jpg", ".webp", ".png"}


class JobNotFound(AppError):
    def __init__(self) -> None:
        super().__init__("job_not_found", "This job does not exist or has expired.", 404)


class JobManager:
    def __init__(self, settings: Settings, provider: MediaProvider, stt: SttProvider | None):
        self.settings = settings
        self.provider = provider
        self.stt = stt
        self.jobs: dict[str, Job] = {}
        self.root = settings.temp_dir / "jobs"
        self._job_slots = asyncio.Semaphore(settings.max_concurrent_jobs)
        self.ffmpeg_slots = asyncio.Semaphore(settings.max_concurrent_ffmpeg)
        self._cleanup_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # Jobs are held in memory; anything on disk is from an interrupted previous run.
        for child in self.root.iterdir():
            shutil.rmtree(child, ignore_errors=True)
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="job-cleanup")

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
        for job in list(self.jobs.values()):
            self.cancel(job)
        runners = [j.runner for j in self.jobs.values() if j.runner and not j.runner.done()]
        if runners:
            await asyncio.wait(runners, timeout=5)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
            try:
                self.cleanup_expired()
            except Exception:  # noqa: BLE001
                log.exception("cleanup failed")

    def cleanup_expired(self, now: float | None = None) -> int:
        now = now or time.time()
        retention = self.settings.retention_minutes * 60
        removed = 0
        for job in list(self.jobs.values()):
            finished = job.finished_at
            expired = (finished is not None and now - finished > retention) or (
                now - job.created_at > retention + self.settings.job_timeout_seconds
            )
            if expired:
                if job.active:
                    self.cancel(job)
                self._delete(job)
                removed += 1
        # Orphaned directories (should not normally exist).
        if self.root.exists():
            for child in self.root.iterdir():
                if child.name not in self.jobs and now - child.stat().st_mtime > retention:
                    shutil.rmtree(child, ignore_errors=True)
        return removed

    def _delete(self, job: Job) -> None:
        self.jobs.pop(job.id, None)
        shutil.rmtree(job.dir, ignore_errors=True)
        log.info("job %s deleted", job.id)

    async def delete(self, job: Job) -> None:
        self.cancel(job)
        if job.runner and not job.runner.done():
            await asyncio.wait([job.runner], timeout=5)
        self._delete(job)

    # ------------------------------------------------------------------ jobs

    def expires_at(self, job: Job) -> float | None:
        if job.finished_at is None:
            return None
        return job.finished_at + self.settings.retention_minutes * 60

    def active_jobs_for(self, client: str) -> int:
        return sum(1 for j in self.jobs.values() if j.client == client and j.active)

    def create_job(self, info: VideoInfo, outputs: Outputs, client: str) -> tuple[Job, str]:
        if self.active_jobs_for(client) >= self.settings.max_active_jobs_per_client:
            raise AppError("too_many_jobs", "You already have several jobs running. Wait for them to finish.", 429)
        job_id = new_job_id()
        token = new_access_token()
        job_dir = self.root / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        tasks: dict = {}
        for kind in TASK_ORDER:
            options = getattr(outputs, kind)
            if options is not None:  # never create work for unselected outputs
                tasks[kind] = TaskState(kind=kind, options=options, stage="Waiting to start")
        job = Job(id=job_id, token_hash=hash_token(token), client=client, info=info, dir=job_dir, tasks=tasks)
        job.sources = SourceCache(self.provider, info, job_dir / "sources")
        self.jobs[job_id] = job
        job.runner = asyncio.create_task(self._run_job(job), name=f"job:{job_id}")
        log.info("job %s created for video %s: %s", job_id, info.video_id, ", ".join(tasks))
        return job, token

    def get(self, job_id: str, token: str | None) -> Job:
        job = self.jobs.get(job_id)
        # Same error for "missing" and "wrong token" so job IDs cannot be probed.
        if job is None or not token_matches(token, job.token_hash):
            raise JobNotFound()
        return job

    def get_unchecked(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise JobNotFound()
        return job

    async def _run_job(self, job: Job) -> None:
        for task in job.tasks.values():
            task.stage = "Waiting for a free worker"
        try:
            async with self._job_slots:
                job.waiting_for_slot = False
                job.started_at = time.time()
                runners = []
                for task in job.tasks.values():
                    if task.cancel_event.is_set():
                        task.status, task.stage = "cancelled", "Cancelled"
                        continue
                    task.runner = asyncio.create_task(self._run_task(job, task), name=f"{job.id}:{task.kind}")
                    runners.append(task.runner)
                if runners:
                    done, pending = await asyncio.wait(runners, timeout=self.settings.job_timeout_seconds)
                    for runner in pending:
                        runner.cancel()
                    if pending:
                        await asyncio.wait(pending, timeout=10)
                        for task in job.tasks.values():
                            if task.status == "cancelled" and not task.error_code and task.runner in pending:
                                task.status = "failed"
                                task.error_code = "timeout"
                                task.error_message = "Processing took longer than the configured time limit."
        finally:
            job.sources.cancel_all()
            shutil.rmtree(job.dir / "sources", ignore_errors=True)
            job.finished_at = time.time()
            log.info("job %s finished: %s", job.id, job.status)

    async def _run_task(self, job: Job, task: TaskState) -> None:
        task.status = "running"
        task.started_at = time.time()
        task.stage = "Starting"
        ctx = TaskContext(job=job, task=task, manager=self)
        try:
            await PROCESSORS[task.kind](ctx)
            task.status = "completed"
            task.progress = 1.0
            task.stage = "Done"
        except (asyncio.CancelledError, CancelledByUser):
            task.status = "cancelled"
            task.stage = "Cancelled"
        except AppError as exc:
            log.info("job %s task %s failed: %s (%s)", job.id, task.kind, exc.code, exc.message)
            task.status, task.stage = "failed", "Failed"
            task.error_code, task.error_message = exc.code, exc.message
        except Exception:
            log.exception("job %s task %s crashed", job.id, task.kind)
            task.status, task.stage = "failed", "Failed"
            task.error_code = "internal_error"
            task.error_message = "An unexpected error occurred while processing this item."
        finally:
            task.finished_at = time.time()
            if task.status != "completed":
                self._discard_task_files(job, task)

    def _discard_task_files(self, job: Job, task: TaskState) -> None:
        for fid in task.file_ids:
            artifact = job.files.pop(fid, None)
            if artifact:
                artifact.path.unlink(missing_ok=True)
        task.file_ids.clear()
        shutil.rmtree(job.dir / task.kind, ignore_errors=True)

    def cancel(self, job: Job, kind: str | None = None) -> int:
        count = 0
        for task in job.tasks.values():
            if (kind and task.kind != kind) or task.done:
                continue
            task.cancel_event.set()
            if task.runner and not task.runner.done():
                task.runner.cancel()
            elif task.status == "queued":
                task.status, task.stage = "cancelled", "Cancelled"
            count += 1
        return count

    # ------------------------------------------------------------------ files

    def register_file(self, job: Job, task: TaskState, path: Path, download_name: str, mime: str,
                      label: str, meta: dict) -> FileArtifact:
        resolved = path.resolve()
        if not resolved.is_relative_to(job.dir.resolve()) or not resolved.is_file():
            raise AppError("internal_error", "Generated file is invalid.", 500)
        artifact = FileArtifact(id=new_file_id(), task=task.kind, path=resolved, download_name=download_name,
                                mime=mime, size=resolved.stat().st_size, label=label, meta=meta)
        job.files[artifact.id] = artifact
        task.file_ids.append(artifact.id)
        return artifact

    def file_for_download(self, job: Job, file_id: str) -> FileArtifact:
        artifact = job.files.get(file_id)
        if artifact is None:
            raise AppError("file_not_found", "This file does not exist or has expired.", 404)
        path = artifact.path.resolve()
        if not path.is_relative_to(job.dir.resolve()) or not path.is_file():
            raise AppError("file_not_found", "This file does not exist or has expired.", 404)
        return artifact

    async def make_zip(self, job: Job, file_ids: list[str] | None) -> FileArtifact:
        candidates = [f for f in job.files.values() if f.task != "zip"]
        if file_ids is not None:
            wanted = set(file_ids)
            candidates = [f for f in candidates if f.id in wanted]
        if not candidates:
            raise AppError("nothing_to_zip", "There are no completed files to package.", 422)

        async with job.zip_lock:
            zip_dir = job.dir / "zip"
            zip_dir.mkdir(exist_ok=True)
            out_path = zip_dir / f"bundle_{new_file_id()}.zip"

            def build() -> None:
                used: set[str] = set()
                with zipfile.ZipFile(out_path, "w", allowZip64=True) as zf:
                    for artifact in candidates:
                        name = artifact.download_name
                        stem, dot, ext = name.rpartition(".")
                        n = 2
                        while name in used:
                            name = f"{stem} ({n}){dot}{ext}"
                            n += 1
                        used.add(name)
                        compress = zipfile.ZIP_STORED if artifact.path.suffix in _STORED_EXTS \
                            else zipfile.ZIP_DEFLATED
                        zf.write(artifact.path, arcname=name, compress_type=compress)

            try:
                await asyncio.wait_for(asyncio.to_thread(build), timeout=1800)
            except Exception:
                out_path.unlink(missing_ok=True)
                log.exception("zip creation failed for job %s", job.id)
                raise AppError("zip_failed", "Could not create the ZIP archive.", 500) from None

        artifact = FileArtifact(
            id=new_file_id(), task="zip", path=out_path.resolve(),
            download_name=f"{safe_stem(job.info.title, job.info.video_id)} [files].zip",
            mime="application/zip", size=out_path.stat().st_size,
            label=f"ZIP · {len(candidates)} file{'s' if len(candidates) != 1 else ''}",
        )
        job.files[artifact.id] = artifact
        return artifact
