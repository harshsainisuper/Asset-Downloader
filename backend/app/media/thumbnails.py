"""Find and download the highest-resolution thumbnail that actually exists.

Not every video has a ``maxresdefault`` image, and YouTube sometimes answers missing
variants with a 120x90 placeholder, so every candidate is fetched and decoded to
verify its real dimensions before the best one is chosen.

Only HTTPS URLs on YouTube's image CDN are fetched, and redirects are not followed.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from PIL import Image

from ..config import get_settings
from ..errors import AppError
from .models import VideoInfo

log = logging.getLogger(__name__)

ThumbnailFormat = Literal["jpg", "original"]
ALLOWED_THUMB_URL = re.compile(r"^https://i[0-9]?\.ytimg\.com/[A-Za-z0-9_\-/.]+(\?[A-Za-z0-9_\-=&%.]*)?$")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Known variants in rough order of expected size.
KNOWN_VARIANTS = ("maxresdefault", "hq720", "oardefault", "sddefault", "hqdefault", "mqdefault", "default")
_PIL_EXT = {"JPEG": "jpg", "WEBP": "webp", "PNG": "png"}


@dataclass
class ThumbnailCandidate:
    url: str
    data: bytes
    width: int
    height: int
    pil_format: str


@dataclass
class ThumbnailResult:
    path: Path
    width: int
    height: int
    ext: str
    converted: bool
    source_url: str


def make_client() -> httpx.AsyncClient:
    # Same outbound proxy as yt-dlp (YTDLP_PROXY), if one is configured.
    proxy = get_settings().ytdlp_proxy or None
    return httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False, proxy=proxy)


def candidate_urls(info: VideoInfo) -> list[str]:
    vid = info.video_id
    urls: list[str] = []
    for name in KNOWN_VARIANTS:
        urls.append(f"https://i.ytimg.com/vi/{vid}/{name}.jpg")
        urls.append(f"https://i.ytimg.com/vi_webp/{vid}/{name}.webp")
    for thumb in info.thumbnails:
        urls.append(thumb.url)
    seen: set[str] = set()
    result = []
    for url in urls:
        if url not in seen and ALLOWED_THUMB_URL.match(url):
            seen.add(url)
            result.append(url)
    return result


async def _fetch(client: httpx.AsyncClient, url: str) -> ThumbnailCandidate | None:
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
                return None
            declared = int(resp.headers.get("content-length") or 0)
            if declared > MAX_IMAGE_BYTES:
                return None
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_IMAGE_BYTES:
                    return None
    except httpx.HTTPError as exc:
        log.debug("thumbnail candidate %s failed: %s", url, exc)
        return None
    try:
        with Image.open(io.BytesIO(buf)) as img:
            img.verify()
        with Image.open(io.BytesIO(buf)) as img:
            width, height, fmt = img.width, img.height, (img.format or "").upper()
    except Exception:  # noqa: BLE001 - any decode failure just disqualifies the candidate
        return None
    if fmt not in _PIL_EXT:
        return None
    # YouTube's "missing image" placeholder is 120x90; only accept that size for "default".
    if (width, height) == (120, 90) and "/default." not in url:
        return None
    return ThumbnailCandidate(url=url, data=bytes(buf), width=width, height=height, pil_format=fmt)


def choose_best(candidates: list[ThumbnailCandidate]) -> ThumbnailCandidate:
    def rank(c: ThumbnailCandidate) -> tuple:
        # Most pixels wins; on ties prefer JPEG (universal, avoids conversion).
        return (c.width * c.height, c.pil_format == "JPEG")

    return max(candidates, key=rank)


async def download_best_thumbnail(
    info: VideoInfo,
    dest_dir: Path,
    out_stem: str,
    fmt: ThumbnailFormat,
    on_progress: Callable[[float], None] | None = None,
    client: httpx.AsyncClient | None = None,
) -> ThumbnailResult:
    urls = candidate_urls(info)
    owns_client = client is None
    client = client or make_client()
    sem = asyncio.Semaphore(6)
    done = 0

    async def guarded(url: str) -> ThumbnailCandidate | None:
        nonlocal done
        async with sem:
            result = await _fetch(client, url)
        done += 1
        if on_progress:
            on_progress(done / len(urls) * 0.9)
        return result

    try:
        results = await asyncio.gather(*(guarded(u) for u in urls))
    finally:
        if owns_client:
            await client.aclose()

    valid = [c for c in results if c is not None]
    if not valid:
        raise AppError("thumbnail_unavailable", "No thumbnail image could be retrieved for this video.", 502)
    best = choose_best(valid)

    dest_dir.mkdir(parents=True, exist_ok=True)
    converted = False
    if fmt == "jpg" and best.pil_format != "JPEG":
        with Image.open(io.BytesIO(best.data)) as img:
            out = io.BytesIO()
            img.convert("RGB").save(out, "JPEG", quality=95, optimize=True)
        data, ext, converted = out.getvalue(), "jpg", True
    else:
        data, ext = best.data, _PIL_EXT[best.pil_format]

    path = dest_dir / f"{out_stem}.{ext}"
    path.write_bytes(data)
    if on_progress:
        on_progress(1.0)
    return ThumbnailResult(path=path, width=best.width, height=best.height, ext=ext,
                           converted=converted, source_url=best.url)
