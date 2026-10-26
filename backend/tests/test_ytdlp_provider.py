"""Exercise the real yt-dlp provider code paths without contacting YouTube.

A yt-dlp style info dict whose stream URLs point at a local HTTP server is fed to
``download_format``/``download_captions`` (the same ``process_ie_result`` path used
for real videos), and ``_normalize`` is checked against a realistic payload.
"""

from __future__ import annotations

import functools
import http.server
import shutil
import threading

import anyio
import pytest

from app.config import Settings
from app.errors import CancelledByUser, VideoUnavailableError
from app.media.ytdlp_provider import YtDlpProvider
from app.url_validation import VideoRef

from .conftest import SAMPLE_JSON3, VIDEO_ID, requires_ffmpeg


@pytest.fixture
def local_server(tmp_path, media_files, monkeypatch):
    root = tmp_path / "www"
    root.mkdir()
    shutil.copy(media_files["h264"], root / "video.mp4")
    shutil.copy(media_files["aac"], root / "audio.m4a")
    (root / "subs.json3").write_text(SAMPLE_JSON3)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[assignment]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def raw_info(base: str) -> dict:
    return {
        "id": VIDEO_ID,
        "title": "Local test",
        "channel": "Chan",
        "duration": 2,
        "extractor": "youtube",
        "extractor_key": "Youtube",
        "webpage_url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
        "width": 1920, "height": 1080,
        "thumbnail": f"https://i.ytimg.com/vi/{VIDEO_ID}/maxresdefault.jpg",
        "thumbnails": [{"url": f"https://i.ytimg.com/vi/{VIDEO_ID}/hq720.jpg"}, {"url": "https://evil.com/x.jpg"}],
        "formats": [
            {"format_id": "137", "url": f"{base}/video.mp4", "ext": "mp4", "vcodec": "avc1.640028",
             "acodec": "none", "width": 1920, "height": 1080, "format_note": "1080p", "protocol": "http"},
            {"format_id": "140", "url": f"{base}/audio.m4a", "ext": "m4a", "vcodec": "none",
             "acodec": "mp4a.40.2", "abr": 129.5, "protocol": "http", "language_preference": 10},
            {"format_id": "sb0", "url": f"{base}/sb", "ext": "mhtml", "vcodec": "none", "acodec": "none",
             "format_note": "storyboard", "protocol": "mhtml"},
            {"format_id": "999", "url": f"{base}/drm.mp4", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
             "has_drm": True},
        ],
        "subtitles": {"en": [{"ext": "json3", "url": f"{base}/subs.json3", "name": "English"}],
                      "live_chat": [{"ext": "json", "url": f"{base}/chat"}]},
        "automatic_captions": {
            "en-orig": [{"ext": "json3", "url": f"{base}/subs.json3", "name": "English (Original)"}],
            "en": [{"ext": "json3", "url": f"{base}/subs.json3", "name": "English"}],
            "de": [{"ext": "json3", "url": f"{base}/subs.json3", "name": "German"}],
        },
    }


@pytest.fixture
def ytdlp(tmp_path):
    provider = YtDlpProvider(Settings(_env_file=None, temp_dir=tmp_path / "t", ytdlp_js_runtimes=""))
    yield provider
    provider.shutdown()


def test_normalize_realistic_payload(ytdlp, local_server):
    info = ytdlp._normalize(raw_info(local_server), VideoRef(VIDEO_ID))
    assert [f.format_id for f in info.formats] == ["137", "140"]  # storyboard and DRM removed
    assert info.formats[0].acodec is None and info.formats[1].vcodec is None
    assert [t.url for t in info.thumbnails] == [f"https://i.ytimg.com/vi/{VIDEO_ID}/hq720.jpg"]
    tracks = {c.track_id: c for c in info.captions}
    # Manual track + only the original-language automatic track (no machine translations, no live chat).
    assert set(tracks) == {"manual:en", "auto:en-orig"}
    assert tracks["auto:en-orig"].is_original and info.original_language == "en"
    assert not info.is_short


def test_vertical_short_detection(ytdlp, local_server):
    raw = raw_info(local_server) | {"width": 1080, "height": 1920, "duration": 30}
    assert ytdlp._normalize(raw, VideoRef(VIDEO_ID)).is_short


def test_drm_only_video_rejected(ytdlp, local_server):
    raw = raw_info(local_server)
    raw["formats"] = [f for f in raw["formats"] if f["format_id"] == "999"]
    with pytest.raises(VideoUnavailableError) as exc:
        ytdlp._normalize(raw, VideoRef(VIDEO_ID))
    assert exc.value.code == "drm_protected"


@requires_ffmpeg
def test_download_format_and_captions(ytdlp, local_server, tmp_path, media_files):
    info = ytdlp._normalize(raw_info(local_server), VideoRef(VIDEO_ID))
    progress: list[tuple[int, int | None]] = []

    async def run():
        cancel = threading.Event()
        video = await ytdlp.download_format(info, "137", tmp_path / "src", lambda d, t: progress.append((d, t)),
                                            cancel)
        subs = await ytdlp.download_captions(info, info.caption_by_id("manual:en"), tmp_path / "caps", cancel)
        return video, subs

    video, subs = anyio.run(run)
    assert video.name == "src_137.mp4"
    assert video.read_bytes() == media_files["h264"].read_bytes()
    assert progress and progress[-1][0] == progress[-1][1]
    assert subs.suffix == ".json3" and "Hello and welcome" in subs.read_text()


def test_download_cancelled(ytdlp, local_server, tmp_path):
    info = ytdlp._normalize(raw_info(local_server), VideoRef(VIDEO_ID))
    cancel = threading.Event()
    cancel.set()

    async def run():
        await ytdlp.download_format(info, "140", tmp_path / "src", lambda d, t: None, cancel)

    with pytest.raises(CancelledByUser):
        anyio.run(run)
