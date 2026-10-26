import io

import anyio
import httpx
import pytest
from PIL import Image

from app.errors import AppError, map_provider_error
from app.filenames import build_filename, safe_stem
from app.media.models import Thumbnail
from app.media.thumbnails import candidate_urls, download_best_thumbnail

from .conftest import VIDEO_ID, make_info


def _img(w: int, h: int, fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buf, fmt)
    return buf.getvalue()


def _client(routes: dict[str, tuple[bytes, str]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, (data, ctype) in routes.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(200, content=data, headers={"content-type": ctype})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_candidate_urls_only_youtube_cdn():
    info = make_info(thumbnails=[Thumbnail("https://i.ytimg.com/vi/x/custom.jpg"),
                                 Thumbnail("https://evil.com/x.jpg"),
                                 Thumbnail("http://i.ytimg.com/vi/x/plain-http.jpg")])
    urls = candidate_urls(info)
    assert f"https://i.ytimg.com/vi/{VIDEO_ID}/maxresdefault.jpg" in urls
    assert "https://i.ytimg.com/vi/x/custom.jpg" in urls
    assert all(u.startswith("https://i.ytimg.com/") for u in urls)


def test_best_thumbnail_picks_largest_valid_and_skips_placeholder(tmp_path):
    client = _client({
        "/maxresdefault.jpg": (_img(120, 90, "JPEG"), "image/jpeg"),  # placeholder
        "/hq720.webp": (_img(1280, 720, "WEBP"), "image/webp"),
        "/sddefault.jpg": (_img(640, 480, "JPEG"), "image/jpeg"),
        "/hqdefault.jpg": (_img(480, 360, "JPEG"), "image/jpeg"),
    })

    async def run(fmt):
        return await download_best_thumbnail(make_info(), tmp_path / fmt, "thumb", fmt, client=client)

    jpg = anyio.run(run, "jpg")
    assert (jpg.width, jpg.height, jpg.ext, jpg.converted) == (1280, 720, "jpg", True)
    with Image.open(jpg.path) as im:
        assert im.format == "JPEG" and im.size == (1280, 720)

    original = anyio.run(run, "original")
    assert (original.ext, original.converted) == ("webp", False)


def test_thumbnail_none_available(tmp_path):
    async def run():
        return await download_best_thumbnail(make_info(), tmp_path, "t", "jpg", client=_client({}))

    with pytest.raises(AppError) as exc:
        anyio.run(run)
    assert exc.value.code == "thumbnail_unavailable"


def test_non_image_responses_rejected(tmp_path):
    client = _client({"/maxresdefault.jpg": (b"<html>nope</html>", "text/html"),
                      "/hqdefault.jpg": (b"not really an image", "image/jpeg"),
                      "/mqdefault.jpg": (_img(320, 180, "JPEG"), "image/jpeg")})

    async def run():
        return await download_best_thumbnail(make_info(), tmp_path, "t", "jpg", client=client)

    assert anyio.run(run).width == 320


@pytest.mark.parametrize("msg,code", [
    ("Private video. Sign in if you've been granted access", "video_private"),
    ("Sign in to confirm your age. This video may be inappropriate for some users.", "age_restricted"),
    ("Join this channel to get access to members-only content", "members_only"),
    ("This video has been removed by the uploader", "video_removed"),
    ("Video unavailable. This video is not available", "video_unavailable"),
    ("The uploader has not made this video available in your country", "geo_restricted"),
    ("Sign in to confirm you're not a bot", "provider_blocked"),
    ("HTTP Error 429: Too Many Requests", "provider_blocked"),
    ("Requested format is not available", "format_unavailable"),
    ("Premieres in 3 hours", "not_started"),
    ("Unable to download API page: timed out", "network_error"),
    ("something nobody anticipated", "provider_error"),
])
def test_provider_error_mapping(msg, code):
    err = map_provider_error(Exception(f"ERROR: [youtube] abc: {msg}"))
    assert err.code == code
    assert "[youtube]" not in err.message


def test_safe_filenames():
    name = safe_stem("../../etc/passwd")
    assert "/" not in name and ".." not in name
    assert safe_stem('a<b>c:"d"|e?f*g') == "a b c d e f g"
    assert safe_stem("CON") == "download"
    assert safe_stem("‮evil‭txt") == "eviltxt"
    assert safe_stem("") == "download"
    assert len(safe_stem("é" * 500).encode()) <= 150
    assert build_filename("My/Video", "abc", "1080p", "mp4") == "My Video [1080p].mp4"
    assert build_filename(None, "abc", "", "MP3") == "abc.mp3"
