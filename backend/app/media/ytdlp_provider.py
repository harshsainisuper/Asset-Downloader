"""MediaProvider implementation backed by yt-dlp.

Security notes:
* Only canonical ``https://www.youtube.com/watch?v=<id>`` URLs (rebuilt from a validated
  ID) are passed to yt-dlp, and yt-dlp is restricted to its ``youtube`` extractor, so the
  generic extractor can never be used to fetch arbitrary URLs.
* No cookies or account credentials are used: private, members-only and age-restricted
  videos are reported as unsupported rather than circumvented. DRM formats are skipped.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import logging
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError

from ..config import Settings
from ..errors import AppError, CancelledByUser, VideoUnavailableError, map_provider_error
from ..url_validation import VideoRef
from .provider import ByteProgress, MediaProvider
from .models import CaptionTrack, MediaFormat, Thumbnail, VideoInfo

log = logging.getLogger(__name__)

_ALLOWED_THUMB_HOSTS = re.compile(r"^https://i[0-9]?\.ytimg\.com/", re.I)
_CAPTION_FORMAT_PREF = "json3/vtt/srv3/best"


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

    # ------------------------------------------------------------------ options

    def _js_runtimes(self) -> dict[str, dict]:
        names = [n.strip().lower() for n in self.settings.ytdlp_js_runtimes.split(",") if n.strip()]
        return {n: {} for n in names if n in ("deno", "node", "bun", "quickjs")}

    def _base_opts(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "noplaylist": True,
            "allowed_extractors": ["youtube"],
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
        return opts

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
            cached = self._info_cache.get(ref.video_id)
            if cached and time.monotonic() - cached[0] < ttl:
                info = copy.copy(cached[1])
                info.is_short = info.is_short or ref.is_short
                return info

        raw = await self._extract_raw(ref)
        info = self._normalize(raw, ref)
        with self._cache_lock:
            now = time.monotonic()
            for key in [k for k, (ts, _) in self._info_cache.items() if now - ts >= ttl]:
                del self._info_cache[key]
            self._info_cache[ref.video_id] = (now, info)
        return info

    async def _extract_raw(self, ref: VideoRef) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            with yt_dlp.YoutubeDL(self._base_opts()) as ydl:
                data = ydl.extract_info(ref.canonical_url, download=False)
                return yt_dlp.YoutubeDL.sanitize_info(data, remove_private_keys=True)

        try:
            raw = await self._run(work, timeout=self.settings.info_timeout_seconds)
        except TimeoutError:
            raise AppError("timeout", "YouTube took too long to respond. Please try again.", 504) from None
        except (DownloadError, ExtractorError) as exc:
            log.warning("info extraction failed for %s: %s", ref.video_id, exc)
            raise map_provider_error(exc) from None

        if not isinstance(raw, dict) or raw.get("_type", "video") != "video":
            raise VideoUnavailableError("unsupported_content", "That link does not point to a single video.")
        if raw.get("id") != ref.video_id:
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

        thumbs = [
            Thumbnail(url=t["url"], width=t.get("width"), height=t.get("height"))
            for t in raw.get("thumbnails") or []
            if isinstance(t.get("url"), str) and _ALLOWED_THUMB_HOSTS.match(t["url"])
        ]
        thumb_url = raw.get("thumbnail") if isinstance(raw.get("thumbnail"), str) else None
        if thumb_url and not _ALLOWED_THUMB_HOSTS.match(thumb_url):
            thumb_url = None
        if not thumb_url:
            thumb_url = f"https://i.ytimg.com/vi/{ref.video_id}/hqdefault.jpg"

        captions, original_language = self._captions(raw)
        duration = raw.get("duration")
        width, height = raw.get("width"), raw.get("height")
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
            opts = self._base_opts() | {
                "format": format_id,
                "outtmpl": {"default": str(dest_dir).replace("%", "%%") + f"/{stem}.%(ext)s"},
                "progress_hooks": [hook],
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
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
                    with self._cache_lock:
                        self._info_cache.pop(info.video_id, None)
                    raw = await self._extract_raw(VideoRef(info.video_id))
                    continue
                raise map_provider_error(exc) from None

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
            opts = self._base_opts() | {
                "skip_download": True,
                "writesubtitles": track.kind == "manual",
                "writeautomaticsub": track.kind == "auto",
                "subtitleslangs": [re.escape(track.key)],
                "subtitlesformat": _CAPTION_FORMAT_PREF,
                "outtmpl": {"default": prefix + "/captions.%(ext)s", "subtitle": prefix + "/captions.%(ext)s"},
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.process_ie_result(copy.deepcopy(info.raw), download=True)

        try:
            await self._run(work, timeout=self.settings.info_timeout_seconds * 2)
        except DownloadCancelled:
            raise CancelledByUser() from None
        except TimeoutError:
            raise AppError("timeout", "Downloading captions timed out.", 504) from None
        except (DownloadError, ExtractorError) as exc:
            log.warning("caption download failed for %s/%s: %s", info.video_id, track.key, exc)
            raise map_provider_error(exc) from None

        files = [p for p in dest_dir.glob("captions.*") if not p.name.endswith((".part", ".ytdl"))]
        if not files:
            raise AppError("captions_unavailable", "YouTube did not return the selected caption track.", 502)
        return files[0]

    def diagnostics(self) -> dict[str, object]:
        runtimes = {name: bool(shutil.which(name)) for name in self._js_runtimes()}
        return {
            "name": self.name,
            "version": yt_dlp.version.__version__,
            "js_runtimes": runtimes,
            "js_runtime_available": any(runtimes.values()),
        }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
