"""MediaProvider implementation backed by yt-dlp.

Security notes:
* Only canonical ``https://www.youtube.com/watch?v=<id>`` or ``https://www.instagram.com/p/<id>/``
  URLs (rebuilt from a validated ID) are passed to yt-dlp, and yt-dlp is restricted to that
  platform's extractor, so the generic extractor can never be used to fetch arbitrary URLs.
* No cookies or account credentials are used: private, members-only and age-restricted
  videos are reported as unsupported rather than circumvented. DRM formats are skipped.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import functools
import importlib.metadata
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError

from ..config import Settings
from ..errors import AppError, CancelledByUser, VideoUnavailableError, map_provider_error
from ..url_validation import PLATFORM_NAMES, VideoRef
from . import thumbnails
from .provider import ByteProgress, MediaProvider
from .models import CaptionTrack, MediaFormat, Thumbnail, VideoInfo

log = logging.getLogger(__name__)

_ALLOWED_THUMB_HOSTS = {
    "youtube": re.compile(r"^https://i[0-9]?\.ytimg\.com/", re.I),
    "instagram": re.compile(r"^https://(?:[a-z0-9-]+\.)*(?:cdninstagram\.com|fbcdn\.net)/", re.I),
}
# yt-dlp extractor names (matched as regexes) allowed for each platform.
_EXTRACTORS = {"youtube": ["youtube"], "instagram": ["Instagram"]}
_CAPTION_FORMAT_PREF = "json3/vtt/srv3/best"
# Where Render mounts Secret Files for Docker services; used when YTDLP_COOKIES_FILE is unset.
DEFAULT_COOKIE_PATHS = (Path("/etc/secrets/cookies.txt"),)


class _YtdlpLogger:
    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.warning("yt-dlp error: %s", msg)


class YtDlpProvider(MediaProvider):
    name = "yt-dlp"

    def __init__(self, settings: Settings, max_workers: int = 8):
        self.settings = settings
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ytdlp")
        self._info_cache: dict[str, tuple[float, VideoInfo]] = {}
        self._cache_lock = threading.Lock()
        self._cookie_dir = settings.temp_dir / "cookies"
        self.default_cookie_paths: tuple[Path, ...] = DEFAULT_COOKIE_PATHS

    # ------------------------------------------------------------------ options

    def _js_runtimes(self) -> dict[str, dict]:
        names = [n.strip().lower() for n in self.settings.ytdlp_js_runtimes.split(",") if n.strip()]
        return {n: {} for n in names if n in ("deno", "node", "bun", "quickjs")}

    def _base_opts(self, platform: str = "youtube") -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "noplaylist": True,
            "allowed_extractors": _EXTRACTORS[platform],
            "socket_timeout": self.settings.ytdlp_socket_timeout,
            "logger": _YtdlpLogger(),
            "retries": 3,
            "fragment_retries": 3,
            "overwrites": True,
            "continuedl": False,
            "max_filesize": self.settings.max_filesize_bytes,
            "js_runtimes": self._js_runtimes(),
            "check_formats": False,
        }
        if self.settings.ffmpeg_binary:
            opts["ffmpeg_location"] = self.settings.ffmpeg_binary
        if self.settings.ytdlp_proxy:
            opts["proxy"] = self.settings.ytdlp_proxy
        extractor_args: dict[str, dict[str, list[str]]] = {}
        clients = [c.strip() for c in self.settings.ytdlp_player_clients.split(",") if c.strip()]
        if clients:
            extractor_args["youtube"] = {"player_client": clients}
        if self.settings.pot_provider == "bgutil":
            extractor_args["youtubepot-bgutilhttp"] = {"base_url": [self.settings.bgutil_base_url]}
        if extractor_args:
            opts["extractor_args"] = extractor_args
        return opts

    def _cookie_candidate(self) -> Path | None:
        if self.settings.ytdlp_cookies_file:
            return Path(self.settings.ytdlp_cookies_file)
        return next((p for p in self.default_cookie_paths if p.exists()), None)

    @property
    def cookie_status(self) -> str:
        """One of: configured, missing, unreadable, not_configured."""
        path = self._cookie_candidate()
        if path is None:
            return "not_configured"
        if not path.is_file():
            return "missing"
        if not os.access(path, os.R_OK):
            return "unreadable"
        return "configured"

    @property
    def cookies_path(self) -> Path | None:
        return self._cookie_candidate() if self.cookie_status == "configured" else None

    @contextlib.contextmanager
    def _ydl(self, extra: dict[str, Any] | None = None, platform: str = "youtube") -> Iterator[yt_dlp.YoutubeDL]:
        opts = self._base_opts(platform) | (extra or {})
        cookie_copy: Path | None = None
        if self.cookies_path:
            # yt-dlp rewrites its cookie file on exit. Give every instance a private copy:
            # the original may be read-only (Render Secret Files) and jobs run concurrently.
            try:
                self._cookie_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd, name = tempfile.mkstemp(prefix="cookies-", suffix=".txt", dir=self._cookie_dir)
                os.close(fd)
                cookie_copy = Path(name)
                shutil.copyfile(self.cookies_path, name)
                opts["cookiefile"] = name
            except OSError as exc:
                log.error("could not use YTDLP_COOKIES_FILE (%s); continuing without cookies", exc)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                yield ydl
        finally:
            if cookie_copy:
                cookie_copy.unlink(missing_ok=True)

    async def _run(self, fn, timeout: float | None = None):
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._executor, fn)
        if timeout is None:
            return await fut
        return await asyncio.wait_for(fut, timeout=timeout)

    # ------------------------------------------------------------------ metadata

    async def fetch_info(self, ref: VideoRef) -> VideoInfo:
        ttl = self.settings.info_cache_seconds
        with self._cache_lock:
            cached = self._info_cache.get(ref.cache_key)
            if cached and time.monotonic() - cached[0] < ttl:
                info = copy.copy(cached[1])
                info.is_short = info.is_short or ref.is_short
                return info

        raw = await self._extract_raw(ref)
        info = self._normalize(raw, ref)
        if info.platform == "instagram":
            info.thumbnail_url = await self._inline_preview(info)
        with self._cache_lock:
            now = time.monotonic()
            for key in [k for k, (ts, _) in self._info_cache.items() if now - ts >= ttl]:
                del self._info_cache[key]
            self._info_cache[ref.cache_key] = (now, info)
        return info

    @staticmethod
    async def _inline_preview(info: VideoInfo) -> str | None:
        """Instagram's CDN refuses cross-origin image embedding, so the preview is sent as a data URL."""
        sized = sorted((t for t in info.thumbnails if t.width), key=lambda t: t.width or 0)
        pick = next((t.url for t in sized if (t.width or 0) >= 320), info.thumbnail_url)
        if not pick or not thumbnails.ALLOWED_THUMB_URL["instagram"].match(pick):
            return None
        async with thumbnails.make_client() as client:
            found = await thumbnails.fetch_candidate(client, pick)
        if found is None:
            return None
        mime = {"JPEG": "image/jpeg", "WEBP": "image/webp", "PNG": "image/png"}[found.pil_format]
        return f"data:{mime};base64,{base64.b64encode(found.data).decode()}"

    async def _extract_raw(self, ref: VideoRef) -> dict[str, Any]:
        site = PLATFORM_NAMES[ref.platform]

        def work() -> dict[str, Any]:
            with self._ydl(platform=ref.platform) as ydl:
                data = ydl.extract_info(ref.canonical_url, download=False)
                return yt_dlp.YoutubeDL.sanitize_info(data, remove_private_keys=True)

        try:
            raw = await self._run(work, timeout=self.settings.info_timeout_seconds)
        except TimeoutError:
            raise AppError("timeout", f"{site} took too long to respond. Please try again.", 504) from None
        except (DownloadError, ExtractorError) as exc:
            log.warning("info extraction failed for %s: %s", ref.cache_key, exc)
            raise map_provider_error(exc, ref.platform) from None

        if isinstance(raw, dict) and ref.platform == "instagram" and raw.get("_type") == "playlist":
            raise VideoUnavailableError("unsupported_content",
                                        "Instagram posts with several photos or videos (carousels) are not supported. "
                                        "Paste a link to a single-video post or a reel.")
        if not isinstance(raw, dict) or raw.get("_type", "video") != "video":
            raise VideoUnavailableError("unsupported_content", "That link does not point to a single video.")
        # Instagram reports long (private-style) shortcodes by their short form.
        raw_id = str(raw.get("id") or "")
        same = raw_id == ref.video_id or (ref.platform == "instagram" and len(raw_id) >= 5
                                          and ref.video_id.startswith(raw_id))
        if not same:
            log.warning("provider returned id %s for %s", raw.get("id"), ref.video_id)
            raise VideoUnavailableError("video_unavailable", "This video is unavailable.")

        live_status = raw.get("live_status")
        if live_status == "is_live" or raw.get("is_live"):
            raise VideoUnavailableError("live_unsupported",
                                        "Live streams are not supported. Try again after the stream has ended.")
        if live_status == "is_upcoming":
            raise VideoUnavailableError("not_started", "This premiere or live stream has not started yet.")
        restricted = {
            "private": ("video_private", "This video is private."),
            "premium_only": ("premium_only", "This video requires a YouTube Premium subscription."),
            "subscriber_only": ("members_only", "This video is only available to channel members."),
            "needs_auth": ("age_restricted", "This video requires a signed-in account and is not supported."),
        }
        if (availability := raw.get("availability")) in restricted:
            raise VideoUnavailableError(*restricted[availability])
        return raw

    def _normalize(self, raw: dict[str, Any], ref: VideoRef) -> VideoInfo:
        formats: list[MediaFormat] = []
        drm_count = 0
        for f in raw.get("formats") or []:
            if f.get("has_drm"):
                drm_count += 1
                continue
            vcodec = f.get("vcodec")
            acodec = f.get("acodec")
            if ref.platform == "instagram" and f.get("ext") == "mp4" and f.get("width") and vcodec is None:
                # Instagram's progressive MP4s often carry no codec info; they are H.264 with AAC
                # audio (acodec "none" is kept when Instagram says there is no audio).
                vcodec = "avc1"
                acodec = "mp4a" if acodec is None else acodec
            vcodec = None if vcodec in (None, "none") else vcodec
            acodec = None if acodec in (None, "none") else acodec
            note = f.get("format_note") or ""
            if (vcodec is None and acodec is None) or f.get("ext") == "mhtml" or "storyboard" in note.lower():
                continue
            format_id = str(f.get("format_id") or "")
            if not format_id:
                continue
            formats.append(MediaFormat(
                format_id=format_id,
                ext=f.get("ext") or "",
                vcodec=vcodec,
                acodec=acodec,
                width=f.get("width"),
                height=f.get("height"),
                fps=f.get("fps"),
                tbr=f.get("tbr"),
                abr=f.get("abr"),
                asr=f.get("asr"),
                filesize=f.get("filesize") or f.get("filesize_approx"),
                format_note=note,
                dynamic_range=f.get("dynamic_range"),
                language=f.get("language"),
                language_preference=f.get("language_preference") if f.get("language_preference") is not None else -1,
                protocol=f.get("protocol") or "https",
                is_drc="drc" in format_id.lower() or "drc" in note.lower(),
            ))
        if not formats and drm_count:
            raise VideoUnavailableError("drm_protected", "This video is DRM-protected and cannot be downloaded.")

        allowed_thumb = _ALLOWED_THUMB_HOSTS[ref.platform]
        thumbs = [
            Thumbnail(url=t["url"], width=t.get("width"), height=t.get("height"))
            for t in raw.get("thumbnails") or []
            if isinstance(t.get("url"), str) and allowed_thumb.match(t["url"])
        ]
        thumb_url = raw.get("thumbnail") if isinstance(raw.get("thumbnail"), str) else None
        if thumb_url and not allowed_thumb.match(thumb_url):
            thumb_url = None
        if not thumb_url and ref.platform == "youtube":
            thumb_url = f"https://i.ytimg.com/vi/{ref.video_id}/hqdefault.jpg"
        elif not thumb_url and thumbs:
            thumb_url = thumbs[-1].url

        captions, original_language = self._captions(raw)
        duration = raw.get("duration")
        width, height = raw.get("width"), raw.get("height")
        if not (width and height):
            sized = [f for f in formats if f.vcodec and f.width and f.height]
            if sized:
                biggest = max(sized, key=lambda f: f.width * f.height)  # type: ignore[operator]
                width, height = biggest.width, biggest.height
        is_short = ref.is_short or bool(
            duration and duration <= 180 and width and height and height > width
        )
        return VideoInfo(
            video_id=ref.video_id,
            title=str(raw.get("title") or ref.video_id),
            channel=raw.get("channel") or raw.get("uploader"),
            duration=float(duration) if duration else None,
            is_short=is_short,
            thumbnail_url=thumb_url,
            thumbnails=thumbs,
            formats=formats,
            captions=captions,
            original_language=original_language,
            live_status=raw.get("live_status"),
            webpage_url=ref.canonical_url,
            platform=ref.platform,
            raw=raw,
        )

    @staticmethod
    def _captions(raw: dict[str, Any]) -> tuple[list[CaptionTrack], str | None]:
        manual: dict[str, list[dict]] = raw.get("subtitles") or {}
        auto: dict[str, list[dict]] = raw.get("automatic_captions") or {}

        orig_keys = [k for k in auto if k.endswith("-orig")]
        original_language = raw.get("language") or (orig_keys[0][: -len("-orig")] if orig_keys else None)
        base = (original_language or "").split("-")[0].lower() or None

        def make(key: str, entries: list[dict], kind: str, is_original: bool) -> CaptionTrack:
            name = next((e.get("name") for e in entries if e.get("name")), key)
            return CaptionTrack(
                key=key,
                language=key.removesuffix("-orig"),
                name=str(name),
                kind=kind,  # type: ignore[arg-type]
                is_original=is_original,
                formats=sorted({str(e.get("ext")) for e in entries if e.get("ext")}),
            )

        tracks: list[CaptionTrack] = []
        for key, entries in manual.items():
            if key == "live_chat" or not entries:
                continue
            tracks.append(make(key, entries, "manual", bool(base) and key.split("-")[0].lower() == base))

        # Automatic captions include the original ASR track plus ~100 machine translations.
        # Only the original-language ASR track is offered.
        if orig_keys:
            auto_keys = orig_keys
        elif original_language and original_language in auto:
            auto_keys = [original_language]
        elif len(auto) == 1:
            auto_keys = list(auto)
        else:
            auto_keys = []
        for key in auto_keys:
            if auto.get(key):
                tracks.append(make(key, auto[key], "auto", True))

        tracks.sort(key=lambda t: (not t.is_original, t.kind != "manual", t.name.lower()))
        return tracks, original_language

    # ------------------------------------------------------------------ downloads

    async def download_format(
        self,
        info: VideoInfo,
        format_id: str,
        dest_dir: Path,
        on_progress: ByteProgress,
        cancel: threading.Event,
    ) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", format_id)
        stem = f"src_{safe_id}"
        dest_dir.mkdir(parents=True, exist_ok=True)

        def hook(d: dict[str, Any]) -> None:
            if cancel.is_set():
                raise DownloadCancelled("cancelled")
            if d.get("status") == "downloading":
                on_progress(int(d.get("downloaded_bytes") or 0), d.get("total_bytes") or d.get("total_bytes_estimate"))

        def work(raw: dict[str, Any]) -> None:
            opts = {
                "format": format_id,
                "outtmpl": {"default": str(dest_dir).replace("%", "%%") + f"/{stem}.%(ext)s"},
                "progress_hooks": [hook],
            }
            with self._ydl(opts, platform=info.platform) as ydl:
                ydl.process_ie_result(copy.deepcopy(raw), download=True)

        raw = info.raw
        for attempt in (1, 2):
            try:
                await self._run(functools.partial(work, raw))
                break
            except DownloadCancelled:
                raise CancelledByUser() from None
            except (DownloadError, ExtractorError) as exc:
                if cancel.is_set():
                    raise CancelledByUser() from None
                log.warning("download of %s/%s failed (attempt %d): %s", info.video_id, format_id, attempt, exc)
                # Stream URLs expire after a few hours; re-extract once and retry.
                if attempt == 1 and re.search(r"HTTP Error 403|expired", str(exc), re.I):
                    ref = VideoRef(info.video_id, platform=info.platform)  # type: ignore[arg-type]
                    with self._cache_lock:
                        self._info_cache.pop(ref.cache_key, None)
                    raw = await self._extract_raw(ref)
                    continue
                raise map_provider_error(exc, info.platform) from None

        files = [p for p in dest_dir.glob(f"{stem}.*") if not p.name.endswith((".part", ".ytdl"))]
        if not files:
            raise AppError("download_failed", "The media stream could not be downloaded.", 502)
        return max(files, key=lambda p: p.stat().st_size)

    async def download_captions(
        self,
        info: VideoInfo,
        track: CaptionTrack,
        dest_dir: Path,
        cancel: threading.Event,
    ) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        prefix = str(dest_dir).replace("%", "%%")

        def work() -> None:
            if cancel.is_set():
                raise DownloadCancelled("cancelled")
            opts = {
                "skip_download": True,
                "writesubtitles": track.kind == "manual",
                "writeautomaticsub": track.kind == "auto",
                "subtitleslangs": [re.escape(track.key)],
                "subtitlesformat": _CAPTION_FORMAT_PREF,
                "outtmpl": {"default": prefix + "/captions.%(ext)s", "subtitle": prefix + "/captions.%(ext)s"},
            }
            with self._ydl(opts, platform=info.platform) as ydl:
                ydl.process_ie_result(copy.deepcopy(info.raw), download=True)

        try:
            await self._run(work, timeout=self.settings.info_timeout_seconds * 2)
        except DownloadCancelled:
            raise CancelledByUser() from None
        except TimeoutError:
            raise AppError("timeout", "Downloading captions timed out.", 504) from None
        except (DownloadError, ExtractorError) as exc:
            log.warning("caption download failed for %s/%s: %s", info.video_id, track.key, exc)
            raise map_provider_error(exc, info.platform) from None

        files = [p for p in dest_dir.glob("captions.*") if not p.name.endswith((".part", ".ytdl"))]
        if not files:
            raise AppError("captions_unavailable", "YouTube did not return the selected caption track.", 502)
        return files[0]

    @functools.cached_property
    def available_js_runtimes(self) -> dict[str, bool]:
        """Which JS runtimes yt-dlp itself can use. This includes Deno installed from PyPI
        inside a virtualenv that is not on PATH, which a plain PATH lookup would miss."""
        names = self._js_runtimes()
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "js_runtimes": names, "logger": _YtdlpLogger()}) as ydl:
                found = ydl._js_runtimes  # yt-dlp's own detection (private API; fallback below)
            return {name: bool(rt and rt.info) for name, rt in found.items()}
        except Exception:  # noqa: BLE001
            log.debug("yt-dlp JS runtime detection failed; falling back to PATH lookup", exc_info=True)
            return {name: bool(shutil.which(name)) for name in names}

    def diagnostics(self) -> dict[str, object]:
        runtimes = self.available_js_runtimes
        try:
            plugin_version: str | None = importlib.metadata.version("bgutil-ytdlp-pot-provider")
        except importlib.metadata.PackageNotFoundError:
            plugin_version = None
        return {
            "name": self.name,
            "version": yt_dlp.version.__version__,
            "js_runtimes": runtimes,
            "js_runtime_available": any(runtimes.values()),
            # Booleans only: never expose cookie contents or proxy credentials.
            "cookies_status": (cookie_status := self.cookie_status),
            "cookies_configured": cookie_status == "configured",
            "cookies_file_missing": cookie_status in ("missing", "unreadable"),
            "proxy_configured": bool(self.settings.ytdlp_proxy),
            "player_clients": self.settings.ytdlp_player_clients or None,
            "pot_provider": self.settings.pot_provider,
            "pot_plugin_version": plugin_version,
        }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
