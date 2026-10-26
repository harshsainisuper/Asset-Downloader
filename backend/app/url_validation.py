"""Strict validation and normalization of YouTube URLs.

Only the 11-character video ID is extracted from user input. Everything passed on to
the media provider is rebuilt from that ID, so user-controlled hosts, paths or query
strings can never reach the downloader (SSRF protection).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass(frozen=True)
class VideoRef:
    video_id: str
    is_short: bool = False

    @property
    def canonical_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def parse_youtube_url(raw: str) -> VideoRef:
    """Validate a user supplied URL and return a normalized reference.

    Raises InvalidUrlError with a user-friendly message on failure.
    """
    if not isinstance(raw, str):
        raise InvalidUrlError("Please paste a YouTube link.")
    url = raw.strip()
    if not url:
        raise InvalidUrlError("Please paste a YouTube link.")
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
        raise InvalidUrlError("Only http(s) YouTube links are supported.")
    if parts.username is not None or parts.password is not None:
        raise InvalidUrlError("Links containing credentials are not allowed.")
    if port not in (None, 80, 443):
        raise InvalidUrlError("Only standard YouTube links are supported.")

    host = (parts.hostname or "").lower().rstrip(".")
    path_segments = [seg for seg in parts.path.split("/") if seg]
    query = parse_qs(parts.query, keep_blank_values=False)

    video_id: str | None = None
    is_short = False

    if host in _SHORT_HOSTS:
        if len(path_segments) == 1:
            video_id = path_segments[0]
    elif host in _YOUTUBE_HOSTS:
        if path_segments[:1] == ["watch"] and len(path_segments) == 1:
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
    else:
        raise InvalidUrlError("Only YouTube links (youtube.com, youtu.be, YouTube Shorts) are supported.")

    if not video_id:
        raise InvalidUrlError("Couldn't find a video ID in that YouTube link.")
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise InvalidUrlError("The video ID in that link is malformed.")
    return VideoRef(video_id=video_id, is_short=is_short)
