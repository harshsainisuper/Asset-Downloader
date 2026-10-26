"""Safe download filename generation."""

from __future__ import annotations

import re
import unicodedata

_RESERVED_WINDOWS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r"\s+")
MAX_STEM_BYTES = 150


def safe_stem(title: str | None, fallback: str = "download") -> str:
    """Turn an arbitrary title into a filesystem- and header-safe filename stem."""
    text = unicodedata.normalize("NFKC", title or "")
    # Drop control/format characters (e.g. bidi overrides) and path separators.
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = _UNSAFE_CHARS.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip(" .")
    # Prevent anything that could be read as a relative path component.
    text = text.replace("..", ".")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_STEM_BYTES:
        text = encoded[:MAX_STEM_BYTES].decode("utf-8", errors="ignore").rstrip(" .")
    if not text or text.upper() in _RESERVED_WINDOWS:
        text = fallback
    return text


def build_filename(title: str | None, video_id: str, suffix: str, extension: str) -> str:
    """e.g. build_filename("My video", "abc", "1080p", "mp4") -> "My video [1080p].mp4"."""
    stem = safe_stem(title, fallback=video_id)
    ext = re.sub(r"[^a-z0-9]", "", extension.lower()) or "bin"
    label = safe_stem(suffix, fallback="") if suffix else ""
    return f"{stem} [{label}].{ext}" if label else f"{stem}.{ext}"
