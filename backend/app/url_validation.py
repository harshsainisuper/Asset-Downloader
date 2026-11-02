"""Strict validation and normalization of YouTube and Instagram URLs.

Only the ID is extracted from user input. Everything passed on to the media provider is
rebuilt from that ID, so user-controlled hosts, paths or query strings can never reach
the downloader (SSRF protection).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from .errors import InvalidUrlError

MAX_URL_LENGTH = 2048
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
# Path prefixes of the form /<prefix>/<video id>
_PATH_ID_PREFIXES = {"shorts", "embed", "v", "live", "e"}

_INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"}
# Post kinds of the form /<kind>/<shortcode>, optionally preceded by /<username>.
_INSTAGRAM_KINDS = {"p", "reel", "reels", "tv"}
INSTAGRAM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{5,64}$")

Platform = Literal["youtube", "instagram"]
PLATFORM_NAMES: dict[str, str] = {"youtube": "YouTube", "instagram": "Instagram"}


@dataclass(frozen=True)
class VideoRef:
    video_id: str
    is_short: bool = False
    platform: Platform = "youtube"

    @property
    def canonical_url(self) -> str:
        if self.platform == "instagram":
            return f"https://www.instagram.com/p/{self.video_id}/"
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def cache_key(self) -> str:
        return f"{self.platform}:{self.video_id}"


def parse_media_url(raw: str) -> VideoRef:
    """Validate a user supplied YouTube or Instagram URL and return a normalized reference.

    Raises InvalidUrlError with a user-friendly message on failure.
    """
    if not isinstance(raw, str):
        raise InvalidUrlError("Please paste a YouTube or Instagram link.")
    url = raw.strip()
    if not url:
        raise InvalidUrlError("Please paste a YouTube or Instagram link.")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidUrlError("That link is too long.")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise InvalidUrlError("The link contains invalid characters.")

    # Allow users to omit the scheme ("youtu.be/xyz").
    if "://" not in url:
        url = "https://" + url

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise InvalidUrlError("That doesn't look like a valid link.") from None

    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidUrlError("Only http(s) links are supported.")
    if parts.username is not None or parts.password is not None:
        raise InvalidUrlError("Links containing credentials are not allowed.")
    if port not in (None, 80, 443):
        raise InvalidUrlError("Only standard YouTube and Instagram links are supported.")

    host = (parts.hostname or "").lower().rstrip(".")
    path_segments = [seg for seg in parts.path.split("/") if seg]

    if host in _SHORT_HOSTS or host in _YOUTUBE_HOSTS:
        return _parse_youtube(host, path_segments, parse_qs(parts.query, keep_blank_values=False))
    if host in _INSTAGRAM_HOSTS:
        return _parse_instagram(path_segments)
    raise InvalidUrlError("Only YouTube (youtube.com, youtu.be, Shorts) and Instagram (posts, reels) links are supported.")


def _parse_youtube(host: str, path_segments: list[str], query: dict[str, list[str]]) -> VideoRef:
    video_id: str | None = None
    is_short = False
    if host in _SHORT_HOSTS:
        if len(path_segments) == 1:
            video_id = path_segments[0]
    elif path_segments[:1] == ["watch"] and len(path_segments) == 1:
        ids = query.get("v")
        if ids:
            video_id = ids[0]
    elif len(path_segments) == 2 and path_segments[0] in _PATH_ID_PREFIXES:
        video_id = path_segments[1]
        is_short = path_segments[0] == "shorts"
    elif path_segments[:1] == ["playlist"]:
        raise InvalidUrlError("Playlists are not supported. Paste a link to a single video.")
    elif not path_segments or path_segments[0].startswith("@") or path_segments[0] in ("channel", "c", "user"):
        raise InvalidUrlError("That link doesn't point to a specific video.")

    if not video_id:
        raise InvalidUrlError("Couldn't find a video ID in that YouTube link.")
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise InvalidUrlError("The video ID in that link is malformed.")
    return VideoRef(video_id=video_id, is_short=is_short)


def _parse_instagram(path_segments: list[str]) -> VideoRef:
    # Share-link codes are not post IDs (resolving them would mean following a redirect).
    if path_segments[:1] == ["share"]:
        raise InvalidUrlError("Instagram share links are not supported. Open the link and copy the post or reel address "
                              "(instagram.com/reel/…).")
    if path_segments[:1] == ["stories"]:
        raise InvalidUrlError("Instagram stories are not supported. Paste a link to a post or reel.")
    # /p/<code>, /reel/<code>, /reels/<code>, /tv/<code>, or the same after /<username>.
    segs = path_segments[1:] if len(path_segments) == 3 else path_segments
    if len(segs) == 2 and segs[0] in _INSTAGRAM_KINDS:
        code = segs[1]
        if not INSTAGRAM_ID_RE.fullmatch(code):
            raise InvalidUrlError("The post ID in that Instagram link is malformed.")
        return VideoRef(video_id=code, platform="instagram")
    raise InvalidUrlError("That Instagram link doesn't point to a specific post or reel.")
