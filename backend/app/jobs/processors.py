"""One processor per output kind. Each only runs when that output was selected."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import Settings
from ..errors import AppError, CancelledByUser, FormatUnavailableError
from ..filenames import build_filename
from ..media import ffmpeg
from ..media.format_selection import (
    estimated_size,
    pick_audio_source,
    plan_audio,
    plan_video,
)
from ..media.provider import MediaProvider
from ..media.thumbnails import download_best_thumbnail
from ..schemas import AudioOptions, ThumbnailOptions, TranscriptOptions, VideoOptions
from ..transcripts.models import ORIGIN_SHORT, Transcript
from ..transcripts.parsers import normalize_segments, parse_caption_file
from ..transcripts.stt import SttError, SttProvider
from ..transcripts.tracks import resolve_track
from ..transcripts.writers import render
from .models import Job, TaskState

if TYPE_CHECKING:
    from .manager import JobManager

log = logging.getLogger(__name__)

MIME = {
    "mp4": "video/mp4", "webm": "video/webm", "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav",
    "jpg": "image/jpeg", "webp": "image/webp", "png": "image/png",
    "txt": "text/plain; charset=utf-8", "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8", "zip": "application/zip",
}


@dataclass
class TaskContext:
    job: Job
    task: TaskState
    manager: JobManager

    @property
    def settings(self) -> Settings:
        return self.manager.settings

    @property
    def provider(self) -> MediaProvider:
        return self.manager.provider

    @property
    def stt(self) -> SttProvider | None:
        return self.manager.stt

    @property
    def work_dir(self) -> Path:
        path = self.job.dir / self.task.kind
        path.mkdir(parents=True, exist_ok=True)
        return path

    def check_cancel(self) -> None:
        if self.task.cancel_event.is_set():
            raise CancelledByUser()

    def progress(self, start: float, end: float, stage: str | None = None) -> Callable[[float], None]:
        if stage:
            self.task.stage = stage

        def update(fraction: float) -> None:
            self.task.set_progress(start + (end - start) * fraction)

        return update

    async def source(self, format_id: str, on_progress: Callable[[float], None]) -> Path:
        self.check_cancel()
        return await self.job.sources.get(format_id, on_progress)

    def add_file(self, path: Path, download_name: str, ext: str, label: str, **meta) -> None:
        self.manager.register_file(self.job, self.task, path, download_name, MIME.get(ext, "application/octet-stream"),
                                   label, meta)


def _check_size(ctx: TaskContext, size: int | None) -> None:
    if size and size > ctx.settings.max_filesize_bytes:
        raise AppError(
            "file_too_large",
            f"This download (~{size / 1024 ** 3:.1f} GB) exceeds the server limit of "
            f"{ctx.settings.max_filesize_mb} MB. Choose a lower quality.",
            422,
        )


# --------------------------------------------------------------------------- video


async def process_video(ctx: TaskContext) -> None:
    opts: VideoOptions = ctx.task.options
    info = ctx.job.info
    plan = plan_video(info.formats, opts.quality, opts.format, opts.h264_compat)
    ctx.task.notes.extend(plan.notes)

    separate_audio = plan.audio is not None and plan.audio.format.format_id != plan.video.format_id
    _check_size(ctx, estimated_size(plan.video, plan.audio.format if separate_audio else None))

    encode_share = 0.5 if plan.video_action == "transcode_h264" else 0.1
    dl_end = 1.0 - encode_share
    video_weight = 0.8 if separate_audio else 1.0
    fractions = {"v": 0.0, "a": 0.0}

    def on_stream(key: str) -> Callable[[float], None]:
        def update(f: float) -> None:
            fractions[key] = f
            combined = fractions["v"] * video_weight + fractions["a"] * (1 - video_weight)
            ctx.task.set_progress(combined * dl_end)

        return update

    ctx.task.stage = "Downloading video and audio streams" if separate_audio else "Downloading video stream"

    if separate_audio:
        assert plan.audio is not None
        video_path, audio_path = await asyncio.gather(
            ctx.source(plan.video.format_id, on_stream("v")),
            ctx.source(plan.audio.format.format_id, on_stream("a")),
        )
    else:
        video_path = await ctx.source(plan.video.format_id, on_stream("v"))
        audio_path = None

    ctx.check_cancel()
    ext = plan.container
    out_path = ctx.work_dir / f"output.{ext}"
    stage = "Re-encoding to H.264" if plan.video_action == "transcode_h264" else "Merging streams"
    async with ctx.manager.ffmpeg_slots:
        await ffmpeg.mux_video(
            plan, video_path, audio_path, out_path, title=info.title, duration=info.duration,
            on_progress=ctx.progress(dl_end, 0.99, stage),
        )

    # Verify the result really contains what the label claims.
    streams = await ffmpeg.probe_streams(out_path)
    if not any(s.codec_type == "video" for s in streams):
        raise AppError("processing_failed", "The merged file has no video stream.", 500)
    if plan.audio_action != "none" and not any(s.codec_type == "audio" for s in streams):
        raise AppError("processing_failed", "The merged file has no audio stream.", 500)

    codecs = plan.video_codec_label + (f" + {plan.audio_codec_label}" if plan.audio_codec_label else "")
    fps = f"{round(plan.video.fps)}fps" if plan.video.fps and plan.video.fps > 30 else ""
    label = f"{ext.upper()} · {plan.tier}p{fps} · {codecs}"
    name = build_filename(info.title, info.video_id, f"{plan.tier}p", ext)
    ctx.add_file(out_path, name, ext, label, quality=plan.tier, codecs=codecs)


# --------------------------------------------------------------------------- audio


async def process_audio(ctx: TaskContext) -> None:
    opts: AudioOptions = ctx.task.options
    info = ctx.job.info
    plan = plan_audio(info.formats, opts.format, opts.mp3_bitrate)
    ctx.task.notes.extend(plan.notes)
    _check_size(ctx, estimated_size(plan.source.format))

    src = await ctx.source(plan.source.format.format_id, ctx.progress(0.0, 0.7, "Downloading audio stream"))
    ctx.check_cancel()
    out_path = ctx.work_dir / f"output.{plan.target}"
    stage = {"mp3": "Converting to MP3", "wav": "Decoding to WAV",
             "m4a": "Remuxing to M4A" if plan.action == "copy" else "Converting to M4A"}[plan.target]
    async with ctx.manager.ffmpeg_slots:
        await ffmpeg.convert_audio(plan, src, out_path, title=info.title, duration=info.duration,
                                   on_progress=ctx.progress(0.7, 0.99, stage))

    if plan.target == "mp3":
        label = f"MP3 · {plan.bitrate_kbps} kbps"
    elif plan.target == "wav":
        label = "WAV · PCM 16-bit"
    else:
        label = "M4A · AAC (original stream)" if plan.action == "copy" else f"M4A · AAC {plan.bitrate_kbps} kbps"
    name = build_filename(info.title, info.video_id, "audio", plan.target)
    ctx.add_file(out_path, name, plan.target, label)


# --------------------------------------------------------------------------- thumbnail


async def process_thumbnail(ctx: TaskContext) -> None:
    opts: ThumbnailOptions = ctx.task.options
    info = ctx.job.info
    ctx.task.stage = "Checking available thumbnail sizes"
    result = await download_best_thumbnail(info, ctx.work_dir, "thumbnail", opts.format,
                                           on_progress=ctx.task.set_progress)
    if result.converted:
        ctx.task.notes.append("The largest thumbnail was WebP; it was converted to JPG.")
    label = f"{result.ext.upper()} · {result.width}×{result.height}"
    name = build_filename(info.title, info.video_id, "thumbnail", result.ext)
    ctx.add_file(result.path, name, result.ext, label, width=result.width, height=result.height)


# --------------------------------------------------------------------------- transcript


def _shared_audio_format_id(ctx: TaskContext) -> str | None:
    """Reuse the audio stream another selected output downloads, if any."""
    formats = ctx.job.info.formats
    for kind in ("audio", "video"):
        other = ctx.job.tasks.get(kind)  # type: ignore[call-overload]
        if other is None:
            continue
        try:
            if kind == "audio":
                return plan_audio(formats, other.options.format, other.options.mp3_bitrate).source.format.format_id
            vplan = plan_video(formats, other.options.quality, other.options.format, other.options.h264_compat)
            if vplan.audio is not None:
                return vplan.audio.format.format_id
        except FormatUnavailableError:
            continue
    src = pick_audio_source(formats)
    return src.format.format_id if src else None


async def _ai_transcript(ctx: TaskContext, start: float) -> Transcript:
    info = ctx.job.info
    stt = ctx.stt
    if stt is None:
        raise AppError("stt_unavailable",
                       "AI transcription is not configured on this server (see README: Speech-to-text).", 422)
    if info.duration and info.duration > ctx.settings.stt_max_duration_seconds:
        raise AppError("too_long_for_stt",
                       "This video is longer than the configured limit for AI transcription.", 422)
    format_id = _shared_audio_format_id(ctx)
    if format_id is None:
        raise AppError("no_audio", "This video has no audio track to transcribe.", 422)

    src = await ctx.source(format_id, ctx.progress(start, start + 0.25, "Downloading audio for transcription"))
    ctx.check_cancel()
    ctx.task.stage = f"Transcribing with {stt.label} (this can take a while)"
    result = await stt.transcribe(
        src, ctx.work_dir / "stt", duration=info.duration, language=info.original_language,
        on_progress=ctx.progress(start + 0.25, 0.97), cancel=ctx.task.cancel_event,
    )
    segments = normalize_segments(result.segments)
    if not segments:
        raise SttError("Transcription produced no text. The audio may contain no recognizable speech.",
                       code="transcription_empty")
    return Transcript(segments=segments, origin="ai_transcript", language=result.language, model=result.model)


async def process_transcript(ctx: TaskContext) -> None:
    opts: TranscriptOptions = ctx.task.options
    info = ctx.job.info
    track = resolve_track(info, opts.track_id)
    transcript: Transcript | None = None
    caption_problem: str | None = None

    if track is not None:
        ctx.task.set_progress(0.05, f"Downloading captions ({track.name})")
        try:
            path = await ctx.provider.download_captions(info, track, ctx.work_dir / "captions",
                                                        ctx.task.cancel_event)
            segments = parse_caption_file(path, rolling=track.kind == "auto")
            if segments:
                transcript = Transcript(
                    segments=segments,
                    origin="manual_captions" if track.kind == "manual" else "auto_captions",
                    language=track.language,
                    language_name=track.name,
                )
            else:
                caption_problem = "The caption track was empty."
        except AppError as exc:
            if not opts.ai_fallback:
                raise
            caption_problem = f"Captions could not be retrieved ({exc.message})"
        ctx.task.set_progress(0.4)

    if transcript is None:
        if not opts.ai_fallback:
            if caption_problem:
                raise AppError("no_captions", caption_problem + " Enable the AI transcription fallback to "
                               "transcribe the audio instead.", 422)
            hint = (" Enable the AI transcription fallback to transcribe the audio." if ctx.stt
                    else " AI transcription is not configured on this server.")
            raise AppError("no_captions", "This video has no captions available." + hint, 422)
        ctx.task.notes.append((caption_problem + " " if caption_problem else "No captions are available. ")
                              + "Using AI transcription instead.")
        transcript = await _ai_transcript(ctx, start=ctx.task.progress)

    ctx.task.notes.append(f"Source: {transcript.description}.")
    lang = transcript.language or "und"
    for fmt in opts.formats:
        out_path = ctx.work_dir / f"transcript.{fmt}"
        out_path.write_text(render(transcript, fmt, info.title), encoding="utf-8")
        suffix = f"{lang} {ORIGIN_SHORT[transcript.origin]}"
        name = build_filename(info.title, info.video_id, suffix, fmt)
        label = f"{fmt.upper()} · {transcript.language_name or lang} · {ORIGIN_SHORT[transcript.origin]}"
        ctx.add_file(out_path, name, fmt, label, origin=transcript.origin, segments=len(transcript.segments))
    ctx.task.set_progress(1.0, "Done")


PROCESSORS: dict[str, Callable[[TaskContext], Awaitable[None]]] = {
    "video": process_video,
    "audio": process_audio,
    "thumbnail": process_thumbnail,
    "transcript": process_transcript,
}
