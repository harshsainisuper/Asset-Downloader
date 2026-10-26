"""FFmpeg/FFprobe helpers.

Every invocation uses an argument list passed straight to ``create_subprocess_exec``;
no shell is involved and no user string is ever concatenated into a command line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from ..errors import AppError
from .format_selection import AudioPlan, VideoPlan

log = logging.getLogger(__name__)

Progress = Callable[[float], None]  # fraction 0..1


class MediaProcessingError(AppError):
    def __init__(self, message: str = "Media processing failed."):
        super().__init__("processing_failed", message, 500)


def _ffmpeg() -> str:
    path = get_settings().ffmpeg_binary
    if not path:
        raise AppError("ffmpeg_missing", "FFmpeg is not installed on the server. See the README.", 500)
    return path


def _ffprobe() -> str:
    path = get_settings().ffprobe_binary
    if not path:
        raise AppError("ffmpeg_missing", "FFprobe is not installed on the server. See the README.", 500)
    return path


def ffmpeg_version() -> str | None:
    path = get_settings().ffmpeg_binary
    if not path:
        return None
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10, check=False)
        return out.stdout.splitlines()[0] if out.stdout else None
    except (OSError, subprocess.SubprocessError):
        return None


async def run_ffmpeg(
    args: list[str],
    *,
    duration: float | None = None,
    on_progress: Progress | None = None,
    timeout: float | None = None,
) -> None:
    cmd = [_ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", *args]
    log.debug("ffmpeg %s", cmd)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stderr_chunks: list[bytes] = []

    async def read_stdout() -> None:
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if on_progress and duration and line.startswith("out_time_us="):
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, seconds / duration)))

    async def read_stderr() -> None:
        assert proc.stderr
        total = 0
        async for raw in proc.stderr:
            if total < 16_384:
                stderr_chunks.append(raw)
                total += len(raw)

    async def kill() -> None:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

    try:
        await asyncio.wait_for(asyncio.gather(read_stdout(), read_stderr(), proc.wait()), timeout=timeout)
    except TimeoutError:
        await kill()
        raise MediaProcessingError("Media processing timed out.") from None
    except BaseException:
        # Cancellation or anything else: never leave ffmpeg running.
        await kill()
        raise

    if proc.returncode != 0:
        log.error("ffmpeg failed (%s): %s", proc.returncode, b"".join(stderr_chunks).decode("utf-8", "replace"))
        raise MediaProcessingError()
    if on_progress:
        on_progress(1.0)


async def probe_duration(path: Path) -> float | None:
    proc = await asyncio.create_subprocess_exec(
        _ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return float(json.loads(out or b"{}")["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return None


@dataclass
class StreamInfo:
    codec_type: str
    codec_name: str
    width: int | None = None
    height: int | None = None


async def probe_streams(path: Path) -> list[StreamInfo]:
    proc = await asyncio.create_subprocess_exec(
        _ffprobe(), "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
        "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    data = json.loads(out or b"{}")
    return [
        StreamInfo(s.get("codec_type", ""), s.get("codec_name", ""), s.get("width"), s.get("height"))
        for s in data.get("streams", [])
    ]


# --------------------------------------------------------------------------- operations


async def mux_video(
    plan: VideoPlan,
    video_path: Path,
    audio_path: Path | None,
    out_path: Path,
    *,
    title: str,
    duration: float | None,
    on_progress: Progress | None,
) -> None:
    settings = get_settings()
    args: list[str] = ["-i", str(video_path)]
    maps = ["-map", "0:v:0"]
    if plan.audio is not None and plan.audio_action != "none":
        if audio_path is not None and audio_path != video_path:
            args += ["-i", str(audio_path)]
            maps += ["-map", "1:a:0"]
        else:
            maps += ["-map", "0:a:0"]
    args += maps

    if plan.video_action == "transcode_h264":
        args += ["-c:v", "libx264", "-preset", settings.x264_preset, "-crf", str(settings.x264_crf),
                 "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", "copy"]

    if plan.audio_action == "copy":
        args += ["-c:a", "copy"]
    elif plan.audio_action == "aac":
        args += ["-c:a", "aac", "-b:a", "192k"]
    elif plan.audio_action == "opus":
        args += ["-c:a", "libopus", "-b:a", "160k"]

    args += ["-dn", "-sn", "-map_metadata", "-1", "-metadata", f"title={title}"]
    if plan.container == "mp4":
        args += ["-movflags", "+faststart", "-f", "mp4"]
    else:
        args += ["-f", "webm"]
    args.append(str(out_path))
    await run_ffmpeg(args, duration=duration, on_progress=on_progress,
                     timeout=get_settings().job_timeout_seconds)


async def convert_audio(
    plan: AudioPlan,
    src_path: Path,
    out_path: Path,
    *,
    title: str,
    duration: float | None,
    on_progress: Progress | None,
) -> None:
    args: list[str] = ["-i", str(src_path), "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
                       "-metadata", f"title={title}"]
    if plan.target == "m4a":
        if plan.action == "copy":
            args += ["-c:a", "copy"]
        else:
            args += ["-c:a", "aac", "-b:a", f"{plan.bitrate_kbps or 256}k"]
        args += ["-movflags", "+faststart", "-f", "mp4"]
    elif plan.target == "mp3":
        args += ["-c:a", "libmp3lame", "-b:a", f"{plan.bitrate_kbps or 320}k", "-id3v2_version", "3", "-f", "mp3"]
    elif plan.target == "wav":
        args += ["-c:a", "pcm_s16le", "-rf64", "auto", "-f", "wav"]
    else:  # pragma: no cover - guarded by plan_audio
        raise MediaProcessingError("Unsupported audio format.")
    args.append(str(out_path))
    await run_ffmpeg(args, duration=duration, on_progress=on_progress,
                     timeout=get_settings().job_timeout_seconds)


async def prepare_stt_chunks(
    src_path: Path, out_dir: Path, chunk_seconds: int, duration: float | None, on_progress: Progress | None
) -> list[tuple[Path, float]]:
    """Downmix to 16 kHz mono MP3 chunks small enough for transcription APIs.

    Returns (chunk_path, start_offset_seconds) pairs.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "chunk_%04d.mp3"
    await run_ffmpeg(
        ["-i", str(src_path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "libmp3lame", "-b:a", "48k", "-f", "segment", "-segment_time", str(chunk_seconds),
         "-reset_timestamps", "1", str(pattern)],
        duration=duration, on_progress=on_progress, timeout=get_settings().job_timeout_seconds,
    )
    chunks = sorted(out_dir.glob("chunk_*.mp3"))
    result: list[tuple[Path, float]] = []
    offset = 0.0
    for chunk in chunks:
        result.append((chunk, offset))
        offset += (await probe_duration(chunk)) or float(chunk_seconds)
    return result
