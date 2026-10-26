"""User-facing error model and mapping of provider/tool failures to safe messages.

Raw exception text from third-party tools is never sent to the client; it is logged
server-side and translated into one of the stable error codes below.
"""

from __future__ import annotations

import re


class AppError(Exception):
    """An error whose message is safe to show to end users."""

    status_code = 400

    def __init__(self, code: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class InvalidUrlError(AppError):
    def __init__(self, message: str):
        super().__init__("invalid_url", message, 422)


class VideoUnavailableError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, 422)


class FormatUnavailableError(AppError):
    def __init__(self, message: str):
        super().__init__("format_unavailable", message, 422)


class CancelledByUser(Exception):
    """Raised inside workers when a task was cancelled."""


# Ordered (pattern, code, message). The first match wins.
_PROVIDER_ERROR_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"private video|this video is private", re.I),
     "video_private", "This video is private. Only videos you can watch publicly are supported."),
    (re.compile(r"confirm your age|age[- ]restricted|inappropriate for some users", re.I),
     "age_restricted",
     "This video is age-restricted and requires a signed-in account. Age-restricted videos are not supported."),
    (re.compile(r"members[- ]only|join this channel|available to this channel's members", re.I),
     "members_only", "This video is only available to channel members."),
    (re.compile(r"premieres in|live event will begin|is_upcoming|scheduled", re.I),
     "not_started", "This premiere or live stream has not started yet."),
    (re.compile(r"removed by the uploader|has been removed|account .* terminated|copyright claim", re.I),
     "video_removed", "This video has been removed and is no longer available."),
    (re.compile(r"available in your country|blocked it in your country|geo.?restrict", re.I),
     "geo_restricted", "This video is not available in the server's region."),
    (re.compile(r"drm|protected content", re.I),
     "drm_protected", "This video is DRM-protected and cannot be downloaded."),
    (re.compile(r"not a bot|sign in to confirm|HTTP Error 429|too many requests|rate.?limit", re.I),
     "provider_blocked",
     "YouTube temporarily refused the request from this server (bot check or rate limit). "
     "Try again later or from a different network."),
    (re.compile(r"requested format is not available|no video formats found", re.I),
     "format_unavailable", "The requested format is not available for this video."),
    (re.compile(r"video unavailable|this video is unavailable|does not exist|incomplete youtube id|"
                r"HTTP Error 404", re.I),
     "video_unavailable", "This video is unavailable. It may have been deleted, made private, or the link is wrong."),
    (re.compile(r"timed out|timeout|connection reset|temporary failure in name resolution|"
                r"network is unreachable|unable to download (webpage|api page)|unable to connect|"
                r"proxy ?error|HTTP Error 5\d\d", re.I),
     "network_error", "Could not reach YouTube. Check the server's internet connection and try again."),
    (re.compile(r"max_filesize|larger than max-filesize|file is larger than", re.I),
     "file_too_large", "The file exceeds the configured maximum size."),
]


def map_provider_error(exc: BaseException) -> AppError:
    """Translate a provider exception into a safe AppError."""
    if isinstance(exc, AppError):
        return exc
    text = str(exc)
    for pattern, code, message in _PROVIDER_ERROR_RULES:
        if pattern.search(text):
            return AppError(code, message, 422)
    return AppError("provider_error", "YouTube could not provide this media. Please try again later.", 502)
