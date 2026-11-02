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


# --------------------------------------------------------------------------- Instagram

IG_ID = "C9xYz_AbC-1"
IG_THUMB = "https://scontent-fra5-1.cdninstagram.com/v/t51.2885-15/1_n.jpg?stp=dst-jpg&oh=00_A&oe=6A"


def instagram_raw(base: str) -> dict:
    """Shaped like yt-dlp's Instagram extractor output: progressive MP4s without codec info + DASH."""
    return {
        "id": IG_ID,
        "title": "Video by someone",
        "channel": "someone",
        "duration": 12.5,
        "extractor": "Instagram",
        "extractor_key": "Instagram",
        "webpage_url": f"https://www.instagram.com/p/{IG_ID}/",
        "http_headers": {"Referer": "https://www.instagram.com/"},
        "thumbnail": IG_THUMB,
        "thumbnails": [{"url": "https://evil.com/x.jpg"},
                       {"url": IG_THUMB.replace("1_n", "small"), "width": 150, "height": 266},
                       {"url": IG_THUMB, "width": 720, "height": 1280}],
        "formats": [
            {"format_id": "101", "url": f"{base}/video.mp4", "ext": "mp4", "width": 720, "height": 1280,
             "protocol": "https"},
            {"format_id": "dash-v", "url": f"{base}/video.mp4", "ext": "mp4", "vcodec": "avc1.4d401f",
             "acodec": "none", "width": 1080, "height": 1920, "protocol": "http"},
            {"format_id": "dash-a", "url": f"{base}/audio.m4a", "ext": "m4a", "vcodec": "none",
             "acodec": "mp4a.40.2", "abr": 128, "protocol": "http"},
        ],
    }


def test_instagram_payload_is_normalized_and_plannable(ytdlp, local_server):
    from app.media.format_selection import available_qualities, plan_audio, plan_video

    info = ytdlp._normalize(instagram_raw(local_server), VideoRef(IG_ID, platform="instagram"))
    assert info.platform == "instagram" and info.webpage_url == f"https://www.instagram.com/p/{IG_ID}/"
    progressive = info.format_by_id("101")
    assert progressive.vcodec == "avc1" and progressive.acodec == "mp4a"  # assumed H.264/AAC
    assert [t.width for t in info.thumbnails] == [150, 720]  # non-Instagram host dropped
    assert info.thumbnail_url == IG_THUMB
    assert info.is_short  # vertical and short
    assert [q.height for q in available_qualities(info.formats)] == [1080, 720]
    plan = plan_video(info.formats, "best", "mp4")
    assert (plan.video.format_id, plan.audio.format.format_id, plan.video_action) == ("dash-v", "dash-a", "copy")
    assert plan_audio(info.formats, "m4a").action == "copy"


def test_instagram_progressive_without_audio_stays_silent(ytdlp, local_server):
    raw = instagram_raw(local_server)
    raw["formats"] = [raw["formats"][0] | {"acodec": "none"}]
    info = ytdlp._normalize(raw, VideoRef(IG_ID, platform="instagram"))
    assert info.formats[0].vcodec == "avc1" and info.formats[0].acodec is None


@requires_ffmpeg
def test_instagram_download_goes_through_yt_dlp(ytdlp, local_server, tmp_path, media_files):
    info = ytdlp._normalize(instagram_raw(local_server), VideoRef(IG_ID, platform="instagram"))

    async def run():
        return await ytdlp.download_format(info, "101", tmp_path / "src", lambda d, t: None, threading.Event())

    path = anyio.run(run)
    assert path.read_bytes() == media_files["h264"].read_bytes()


def test_instagram_only_uses_the_instagram_extractor(ytdlp):
    assert ytdlp._base_opts("instagram")["allowed_extractors"] == ["Instagram"]
    assert ytdlp._base_opts()["allowed_extractors"] == ["youtube"]
    with ytdlp._ydl(platform="instagram") as ydl:
        assert set(ydl._ies) == {"Instagram"}


@pytest.mark.parametrize("raw,code", [
    ({"_type": "playlist", "id": IG_ID, "entries": []}, "unsupported_content"),
    ({"id": "SomethingElse", "formats": []}, "video_unavailable"),
])
def test_instagram_extract_rejects_carousels_and_foreign_ids(ytdlp, monkeypatch, raw, code):
    async def fake_run(fn, timeout=None):
        return raw

    monkeypatch.setattr(ytdlp, "_run", fake_run)
    with pytest.raises(VideoUnavailableError) as exc:
        anyio.run(ytdlp._extract_raw, VideoRef(IG_ID, platform="instagram"))
    assert exc.value.code == code


def test_instagram_long_shortcode_matches_its_short_form(ytdlp, monkeypatch):
    long_code = IG_ID + "x" * 28

    async def fake_run(fn, timeout=None):
        return {"id": IG_ID, "formats": []}

    monkeypatch.setattr(ytdlp, "_run", fake_run)
    assert anyio.run(ytdlp._extract_raw, VideoRef(long_code, platform="instagram"))["id"] == IG_ID


def test_instagram_preview_is_inlined_as_data_url(ytdlp, local_server, monkeypatch):
    from app.media import thumbnails

    fetched: list[str] = []

    async def fake_fetch(client, url):
        fetched.append(url)
        return thumbnails.ThumbnailCandidate(url=url, data=b"\xff\xd8jpeg", width=720, height=1280, pil_format="JPEG")

    async def fake_extract(ref):
        return instagram_raw(local_server)

    monkeypatch.setattr(thumbnails, "fetch_candidate", fake_fetch)
    monkeypatch.setattr(ytdlp, "_extract_raw", fake_extract)
    info = anyio.run(ytdlp.fetch_info, VideoRef(IG_ID, platform="instagram"))
    assert fetched == [IG_THUMB]  # smallest variant that is at least 320 px wide
    assert info.thumbnail_url == "data:image/jpeg;base64,/9hqcGVn"
