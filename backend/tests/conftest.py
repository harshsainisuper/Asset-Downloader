"""Shared fixtures.

The real YouTube provider needs network access, so API/job tests use ``FakeProvider``:
it serves small media files generated locally with FFmpeg through the same
``MediaProvider`` interface. All merging/conversion still runs through real FFmpeg.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import AppError, CancelledByUser
from app.main import create_app
from app.media.format_selection import audio_family, video_family
from app.media.models import CaptionTrack, MediaFormat, VideoInfo
from app.media.provider import MediaProvider
from app.transcripts.models import Segment
from app.transcripts.stt import SttProvider, SttResult
from app.url_validation import VideoRef

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")

VIDEO_ID = "dQw4w9WgXcQ"


def youtube_like_formats() -> list[MediaFormat]:
    """A format list shaped like a real YouTube response (480p intentionally missing)."""
    v = MediaFormat
    return [
        v("139", "m4a", acodec="mp4a.40.5", abr=48.8, asr=22050, filesize=200_000),
        v("140", "m4a", acodec="mp4a.40.2", abr=129.5, asr=44100, filesize=500_000),
        v("251", "webm", acodec="opus", abr=135.2, asr=48000, filesize=520_000),
        v("251-drc", "webm", acodec="opus", abr=140.0, asr=48000, is_drc=True),
        v("18", "mp4", vcodec="avc1.42001E", acodec="mp4a.40.2", width=640, height=360, format_note="360p",
          fps=30, tbr=500),
        v("134", "mp4", vcodec="avc1.4d401e", width=640, height=360, format_note="360p", fps=30, tbr=300),
        v("243", "webm", vcodec="vp9", width=640, height=360, format_note="360p", fps=30, tbr=280),
        v("136", "mp4", vcodec="avc1.64001f", width=1280, height=720, format_note="720p", fps=30, tbr=1500),
        v("247", "webm", vcodec="vp9", width=1280, height=720, format_note="720p", fps=30, tbr=1400),
        v("137", "mp4", vcodec="avc1.640028", width=1920, height=1080, format_note="1080p", fps=30, tbr=4000,
          filesize=3_000_000),
        v("248", "webm", vcodec="vp9", width=1920, height=1080, format_note="1080p", fps=30, tbr=3000),
        v("399", "mp4", vcodec="av01.0.08M.08", width=1920, height=1080, format_note="1080p", fps=30, tbr=2500),
        v("271", "webm", vcodec="vp9", width=2560, height=1440, format_note="1440p", fps=30, tbr=9000),
        v("313", "webm", vcodec="vp9", width=3840, height=2160, format_note="2160p", fps=30, tbr=18000),
        v("401", "mp4", vcodec="av01.0.12M.08", width=3840, height=2160, format_note="2160p", fps=30, tbr=16000),
    ]


def make_info(formats: list[MediaFormat] | None = None, captions: list[CaptionTrack] | None = None,
              **kwargs) -> VideoInfo:
    defaults = dict(
        video_id=VIDEO_ID,
        title='Test Video: "Quotes" / Slashes',
        channel="Test Channel",
        duration=2.0,
        is_short=False,
        thumbnail_url=f"https://i.ytimg.com/vi/{VIDEO_ID}/hqdefault.jpg",
        thumbnails=[],
        formats=formats if formats is not None else youtube_like_formats(),
        captions=captions if captions is not None else [],
        original_language="en",
        webpage_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
    )
    defaults.update(kwargs)
    return VideoInfo(**defaults)


# --------------------------------------------------------------------------- media


def _ff(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media_files(tmp_path_factory) -> dict[str, Path]:
    if FFMPEG is None:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("media")
    src_v = ["-f", "lavfi", "-i", "testsrc=size=160x90:rate=15:duration=2"]
    src_a = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000"]
    files = {
        "h264": d / "h264.mp4",
        "vp9": d / "vp9.webm",
        "av1": d / "av1.mp4",
        "aac": d / "aac.m4a",
        "opus": d / "opus.webm",
        "muxed": d / "muxed.mp4",
    }
    _ff(*src_v, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(files["h264"]))
    _ff(*src_v, "-c:v", "libvpx-vp9", "-b:v", "100k", "-deadline", "realtime", "-an", str(files["vp9"]))
    _ff(*src_v, "-c:v", "libaom-av1", "-cpu-used", "8", "-crf", "50", "-b:v", "0", "-an", str(files["av1"]))
    _ff(*src_a, "-c:a", "aac", "-b:a", "128k", "-vn", str(files["aac"]))
    _ff(*src_a, "-c:a", "libopus", "-b:a", "96k", "-vn", str(files["opus"]))
    _ff(*src_v, *src_a, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(files["muxed"]))
    return files


SAMPLE_JSON3 = """{"events": [
 {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "Hello and welcome."}]},
 {"tStartMs": 1500, "dDurationMs": 500, "segs": [{"utf8": "\\n"}]},
 {"tStartMs": 4000, "dDurationMs": 2000, "segs": [{"utf8": "Second "}, {"utf8": "line &amp; more"}]}
]}"""


class FakeProvider(MediaProvider):
    name = "fake"

    def __init__(self, media: dict[str, Path], info: VideoInfo | None = None):
        self.media = media
        self.info = info or make_info()
        self.fail_info: AppError | Exception | None = None
        self.slow = False
        self.download_calls: list[str] = []
        self.captions: dict[str, tuple[str, str]] = {}

    async def fetch_info(self, ref: VideoRef) -> VideoInfo:
        if self.fail_info is not None:
            raise self.fail_info
        assert ref.video_id == self.info.video_id
        return self.info

    def _source_for(self, fmt: MediaFormat) -> Path:
        if fmt.has_video and fmt.has_audio:
            return self.media["muxed"]
        if fmt.has_video:
            return self.media[{"h264": "h264", "vp9": "vp9", "av1": "av1"}[video_family(fmt.vcodec)]]
        return self.media[{"aac": "aac", "opus": "opus"}[audio_family(fmt.acodec)]]

    async def download_format(self, info, format_id, dest_dir, on_progress, cancel: threading.Event) -> Path:
        self.download_calls.append(format_id)
        fmt = info.format_by_id(format_id)
        assert fmt is not None, format_id
        src = self._source_for(fmt)
        total = src.stat().st_size
        steps = 50 if self.slow else 4
        for i in range(1, steps + 1):
            if cancel.is_set():
                raise CancelledByUser()
            await asyncio.sleep(0.1 if self.slow else 0.01)
            on_progress(total * i // steps, total)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"src_{format_id}{src.suffix}"
        shutil.copyfile(src, dest)
        return dest

    async def download_captions(self, info, track, dest_dir, cancel) -> Path:
        if track.key not in self.captions:
            raise AppError("captions_unavailable", "YouTube did not return the selected caption track.", 502)
        ext, text = self.captions[track.key]
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"captions.{track.key}.{ext}"
        path.write_text(text, encoding="utf-8")
        return path


class FakeStt(SttProvider):
    label = "fake-whisper"

    def __init__(self, segments: list[Segment] | None = None):
        self.segments = segments if segments is not None else [Segment(0.0, 1.5, "Transcribed speech.")]
        self.calls = 0

    async def transcribe(self, audio_path, work_dir, *, duration, language, on_progress, cancel) -> SttResult:
        assert audio_path.exists()
        self.calls += 1
        on_progress(1.0)
        return SttResult(segments=list(self.segments), language="en", model="fake-whisper-1")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        temp_dir=tmp_path / "tmp",
        serve_frontend=False,
        secret_key="test-secret",
        retention_minutes=60,
        info_rate_limit_per_minute=1000,
        job_rate_limit_per_minute=1000,
        max_active_jobs_per_client=50,
        stt_provider="none",
    )


@pytest.fixture
def provider(media_files) -> FakeProvider:
    return FakeProvider(media_files)


@pytest.fixture
def make_client(settings, provider):
    clients = []

    def factory(stt: SttProvider | None = None, **overrides) -> TestClient:
        s = settings.model_copy(update=overrides) if overrides else settings
        app = create_app(settings=s, provider=provider, stt=stt)
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def wait_for_job(client: TestClient, job_id: str, token: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/jobs/{job_id}", headers={"X-Job-Token": token})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data["status"] not in ("queued", "running"):
            return data
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")
