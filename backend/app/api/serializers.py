"""Convert internal objects into JSON-safe API payloads."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from ..config import Settings
from ..jobs.models import FileArtifact, Job
from ..media.format_selection import STANDARD_TIERS, audio_family, available_qualities, pick_audio_source
from ..media.models import VideoInfo
from ..security import sign_download
from ..transcripts.stt import SttProvider
from ..transcripts.tracks import default_track

_PREVIEWABLE = ("video/", "audio/", "image/", "text/", "application/x-subrip")


def info_payload(info: VideoInfo, stt: SttProvider | None) -> dict[str, Any]:
    qualities = available_qualities(info.formats)
    present = {q.height for q in qualities}
    audio = pick_audio_source(info.formats)
    default = default_track(info)
    return {
        "video_id": info.video_id,
        "title": info.title,
        "channel": info.channel,
        "duration": info.duration,
        "is_short": info.is_short,
        "thumbnail_url": info.thumbnail_url,
        "webpage_url": info.webpage_url,
        "video": {
            "available": bool(qualities),
            "qualities": [q.__dict__ for q in qualities],
            "unavailable_standard": [t for t in STANDARD_TIERS if t not in present],
        },
        "audio": {
            "available": audio is not None,
            "source_codec": (audio_family(audio.format.acodec) or "unknown").upper() if audio else None,
            "source_kbps": round(audio.format.abr or audio.format.tbr or 0) or None if audio else None,
        },
        "captions": {
            "tracks": [
                {"id": c.track_id, "name": c.name, "language": c.language, "kind": c.kind,
                 "is_original": c.is_original}
                for c in info.captions
            ],
            "default_track_id": default.track_id if default else None,
        },
        "transcription": {
            "stt_available": stt is not None,
            "stt_label": stt.label if stt else None,
        },
    }


def file_payload(settings: Settings, job: Job, artifact: FileArtifact) -> dict[str, Any]:
    expires = int(time.time()) + settings.download_link_ttl_minutes * 60
    sig = sign_download(settings.signing_key, job.id, artifact.id, expires)
    base = f"/api/files/{job.id}/{artifact.id}"
    query = {"exp": expires, "sig": sig}
    previewable = artifact.mime.startswith(_PREVIEWABLE)
    return {
        "id": artifact.id,
        "task": artifact.task,
        "name": artifact.download_name,
        "mime": artifact.mime.split(";")[0],
        "size": artifact.size,
        "label": artifact.label,
        "meta": artifact.meta,
        "download_url": f"{base}?{urlencode(query)}",
        "preview_url": f"{base}?{urlencode(query | {'inline': 1})}" if previewable else None,
    }


def job_payload(settings: Settings, job: Job, expires_at: float | None) -> dict[str, Any]:
    tasks = []
    for task in job.tasks.values():
        tasks.append({
            "kind": task.kind,
            "status": task.status,
            "progress": round(task.progress, 4),
            "stage": task.stage,
            "error": {"code": task.error_code, "message": task.error_message} if task.error_code else None,
            "notes": list(task.notes),
            "files": [file_payload(settings, job, job.files[fid]) for fid in task.file_ids if fid in job.files],
        })
    return {
        "job_id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "expires_at": expires_at,
        "video": {"id": job.info.video_id, "title": job.info.title},
        "tasks": tasks,
    }
