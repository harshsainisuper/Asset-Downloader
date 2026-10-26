"""Pure functions that decide which source streams to use for each requested output.

Keeping this logic free of I/O makes it easy to unit-test and independent of the
media provider in use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..errors import FormatUnavailableError
from .models import MediaFormat

VideoContainer = Literal["mp4", "webm"]
AudioTarget = Literal["mp3", "m4a", "wav"]
Quality = Literal["best", 2160, 1440, 1080, 720, 480, 360]

STANDARD_TIERS: tuple[int, ...] = (2160, 1440, 1080, 720, 480, 360)
MP3_BITRATES: tuple[int, ...] = (320, 256, 192, 128)

_TIER_RE = re.compile(r"(\d{3,4})p")


def video_family(vcodec: str | None) -> str | None:
    if not vcodec:
        return None
    c = vcodec.lower()
    if c.startswith(("avc1", "avc3", "h264")):
        return "h264"
    if c.startswith(("av01", "av1")):
        return "av1"
    if c.startswith(("vp09", "vp9")):
        return "vp9"
    if c.startswith("vp8"):
        return "vp8"
    if c.startswith(("hev1", "hvc1", "h265", "hevc")):
        return "hevc"
    return "other"


def audio_family(acodec: str | None) -> str | None:
    if not acodec:
        return None
    c = acodec.lower()
    if c.startswith(("mp4a", "aac")):
        return "aac"
    if c.startswith("opus"):
        return "opus"
    if c.startswith("vorbis"):
        return "vorbis"
    if c.startswith(("mp3", "mp4a.40.34")):
        return "mp3"
    if c.startswith(("ac-3", "ec-3", "ac3", "eac3")):
        return "ac3"
    return "other"


def quality_tier(fmt: MediaFormat) -> int | None:
    """YouTube labels quality by the short side (a vertical 1080x1920 Short is "1080p")."""
    m = _TIER_RE.search(fmt.format_note or "")
    if m:
        return int(m.group(1))
    if fmt.width and fmt.height:
        return min(fmt.width, fmt.height)
    return fmt.height


def _is_downloadable_video(fmt: MediaFormat) -> bool:
    return (
        fmt.has_video
        and fmt.protocol not in ("mhtml",)
        and fmt.ext not in ("mhtml",)
        and video_family(fmt.vcodec) in ("h264", "av1", "vp9", "vp8", "hevc")
        and quality_tier(fmt) is not None
    )


def video_formats(formats: list[MediaFormat]) -> list[MediaFormat]:
    return [f for f in formats if _is_downloadable_video(f)]


def audio_only_formats(formats: list[MediaFormat]) -> list[MediaFormat]:
    return [f for f in formats if f.has_audio and not f.has_video and f.protocol != "mhtml"]


# --------------------------------------------------------------------------- qualities


@dataclass
class QualityOption:
    height: int
    label: str
    fps: float | None
    hdr: bool
    mp4: Literal["h264", "av1", "transcode"]  # how an MP4 would be produced
    webm: Literal["native", "unavailable"]
    standard: bool


def _mp4_mode(fams: set[str]) -> Literal["h264", "av1", "transcode"]:
    if "h264" in fams:
        return "h264"
    if "av1" in fams:
        return "av1"
    return "transcode"


def available_qualities(formats: list[MediaFormat]) -> list[QualityOption]:
    by_tier: dict[int, list[MediaFormat]] = {}
    for f in video_formats(formats):
        tier = quality_tier(f)
        assert tier is not None
        by_tier.setdefault(tier, []).append(f)

    options: list[QualityOption] = []
    for tier in sorted(by_tier, reverse=True):
        fmts = by_tier[tier]
        fams = {video_family(f.vcodec) or "" for f in fmts}
        fps_values = [f.fps for f in fmts if f.fps]
        options.append(
            QualityOption(
                height=tier,
                label=f"{tier}p" + (" (4K)" if tier == 2160 else " (8K)" if tier == 4320 else ""),
                fps=max(fps_values) if fps_values else None,
                hdr=any((f.dynamic_range or "SDR").upper() != "SDR" for f in fmts),
                mp4=_mp4_mode(fams),
                webm="native" if fams & {"vp9", "av1", "vp8"} else "unavailable",
                standard=tier in STANDARD_TIERS,
            )
        )
    return options


# --------------------------------------------------------------------------- audio


@dataclass
class AudioSource:
    format: MediaFormat
    embedded_in_video: bool  # True when only muxed audio+video formats exist


def _audio_rank(f: MediaFormat) -> tuple:
    return (
        f.language_preference,  # original language track first
        not f.is_drc,  # avoid dynamic-range-compressed variants
        f.protocol.startswith("http") and "m3u8" not in f.protocol,
        f.abr or f.tbr or 0,
        f.asr or 0,
    )


def pick_audio_source(formats: list[MediaFormat], preferred_family: str | None = None) -> AudioSource | None:
    candidates = audio_only_formats(formats)
    embedded = False
    if not candidates:
        candidates = [f for f in formats if f.has_audio and f.has_video and f.protocol != "mhtml"]
        embedded = True
    if not candidates:
        return None
    top_lang = max(f.language_preference for f in candidates)
    pool = [f for f in candidates if f.language_preference == top_lang]
    if preferred_family:
        preferred = [f for f in pool if audio_family(f.acodec) == preferred_family]
        if preferred:
            pool = preferred
    return AudioSource(format=max(pool, key=_audio_rank), embedded_in_video=embedded)


@dataclass
class AudioPlan:
    source: AudioSource
    target: AudioTarget
    action: Literal["copy", "transcode"]
    bitrate_kbps: int | None
    notes: list[str]


def plan_audio(formats: list[MediaFormat], target: AudioTarget, mp3_bitrate: int = 320) -> AudioPlan:
    if target not in ("mp3", "m4a", "wav"):
        raise FormatUnavailableError(f"Unsupported audio format: {target}")
    if target == "mp3" and mp3_bitrate not in MP3_BITRATES:
        raise FormatUnavailableError("Unsupported MP3 bitrate.")

    source = pick_audio_source(formats, preferred_family="aac" if target == "m4a" else None)
    if source is None:
        raise FormatUnavailableError("This video has no audio track.")
    fam = audio_family(source.format.acodec)
    notes: list[str] = []
    src_desc = f"{(fam or 'unknown').upper()} ~{round(source.format.abr or source.format.tbr or 0)} kbps"

    if target == "m4a":
        if fam == "aac":
            return AudioPlan(source, target, "copy", None, [f"Original {src_desc} stream, no re-encoding."])
        notes.append(f"Source is {src_desc}; re-encoded to AAC 256 kbps.")
        return AudioPlan(source, target, "transcode", 256, notes)
    if target == "mp3":
        notes.append(f"Converted from {src_desc} to MP3 {mp3_bitrate} kbps.")
        if source.format.abr and mp3_bitrate > source.format.abr * 1.6:
            notes.append("A higher MP3 bitrate than the source does not add quality.")
        return AudioPlan(source, target, "transcode", mp3_bitrate, notes)
    notes.append(
        f"Decoded from {src_desc} to uncompressed PCM. WAV does not restore detail lost by the lossy source."
    )
    return AudioPlan(source, target, "transcode", None, notes)


# --------------------------------------------------------------------------- video


@dataclass
class VideoPlan:
    video: MediaFormat
    audio: AudioSource | None
    container: VideoContainer
    tier: int
    video_action: Literal["copy", "transcode_h264"]
    audio_action: Literal["copy", "aac", "opus", "none"]
    notes: list[str]

    @property
    def video_codec_label(self) -> str:
        if self.video_action == "transcode_h264":
            return "H.264"
        return {"h264": "H.264", "av1": "AV1", "vp9": "VP9", "vp8": "VP8"}.get(
            video_family(self.video.vcodec) or "", self.video.vcodec or "?"
        )

    @property
    def audio_codec_label(self) -> str | None:
        if self.audio is None or self.audio_action == "none":
            return None
        if self.audio_action == "aac":
            return "AAC"
        if self.audio_action == "opus":
            return "Opus"
        return (audio_family(self.audio.format.acodec) or "?").upper().replace("OPUS", "Opus")


_MP4_FAMILY_RANK = {"h264": 0, "av1": 1, "vp9": 2, "vp8": 3, "hevc": 4}
_MP4_COMPAT_RANK = {"h264": 0, "vp9": 1, "av1": 2, "vp8": 3, "hevc": 4}
_WEBM_FAMILY_RANK = {"vp9": 0, "av1": 1, "vp8": 2}


def _video_rank(f: MediaFormat, family_rank: dict[str, int]) -> tuple:
    return (
        -family_rank.get(video_family(f.vcodec) or "", 99),
        (f.dynamic_range or "SDR").upper() == "SDR",  # SDR first for compatibility
        not f.has_audio,  # separate (higher quality) streams first
        f.fps or 0,
        f.protocol.startswith("http") and "m3u8" not in f.protocol,
        f.tbr or 0,
    )


def plan_video(
    formats: list[MediaFormat],
    quality: Quality | int | str,
    container: VideoContainer,
    h264_compat: bool = False,
) -> VideoPlan:
    if container not in ("mp4", "webm"):
        raise FormatUnavailableError(f"Unsupported video format: {container}")
    candidates = video_formats(formats)
    if container == "webm":
        candidates = [f for f in candidates if video_family(f.vcodec) in _WEBM_FAMILY_RANK]
    if not candidates:
        if container == "webm" and video_formats(formats):
            raise FormatUnavailableError("WebM is not available for this video. Choose MP4 instead.")
        raise FormatUnavailableError("No downloadable video streams are available for this video.")

    tiers = sorted({quality_tier(f) for f in candidates if quality_tier(f)}, reverse=True)
    if quality == "best":
        target = tiers[0]
    else:
        try:
            target = int(quality)
        except (TypeError, ValueError):
            raise FormatUnavailableError("Invalid video quality.") from None
        if target not in tiers:
            available = ", ".join(f"{t}p" for t in tiers)
            all_tiers = {quality_tier(f) for f in video_formats(formats)}
            if container == "webm" and target in all_tiers:
                raise FormatUnavailableError(f"{target}p is not available as WebM. Choose MP4 or: {available}.")
            raise FormatUnavailableError(f"{target}p is not available for this video. Available: {available}.")

    at_tier = [f for f in candidates if quality_tier(f) == target]
    notes: list[str] = []
    if container == "mp4":
        rank = _MP4_COMPAT_RANK if h264_compat else _MP4_FAMILY_RANK
        video = max(at_tier, key=lambda f: _video_rank(f, rank))
        fam = video_family(video.vcodec)
        if fam == "h264" or (fam == "av1" and not h264_compat):
            video_action: Literal["copy", "transcode_h264"] = "copy"
            if fam == "av1":
                notes.append(
                    f"{target}p is only offered as AV1 here; it is stored in MP4 without re-encoding. "
                    "Enable H.264 compatibility mode for older players."
                )
        else:
            video_action = "transcode_h264"
            notes.append(
                f"{target}p is not offered in an MP4-native codec, so the {(fam or '').upper()} stream is "
                "re-encoded to H.264. This takes longer."
            )
    else:
        video = max(at_tier, key=lambda f: _video_rank(f, _WEBM_FAMILY_RANK))
        video_action = "copy"

    if (video.dynamic_range or "SDR").upper() != "SDR":
        notes.append(f"Only an HDR ({video.dynamic_range}) stream is available at this resolution.")

    preferred_audio = "aac" if container == "mp4" else "opus"
    compatible_audio = {"mp4": {"aac", "mp3", "ac3"}, "webm": {"opus", "vorbis"}}[container]

    audio: AudioSource | None
    if video.has_audio and not audio_only_formats(formats):
        audio = AudioSource(format=video, embedded_in_video=True)
    else:
        # Separate audio-only streams are preferred; without them this falls back to
        # extracting the track from a muxed format.
        audio = pick_audio_source(formats, preferred_family=preferred_audio)

    if audio is None:
        audio_action: Literal["copy", "aac", "opus", "none"] = "none"
        notes.append("This video has no audio track.")
    elif audio_family(audio.format.acodec) in compatible_audio:
        audio_action = "copy"
    else:
        audio_action = "aac" if container == "mp4" else "opus"

    return VideoPlan(
        video=video,
        audio=audio,
        container=container,
        tier=target,
        video_action=video_action,
        audio_action=audio_action,
        notes=notes,
    )


def estimated_size(*fmts: MediaFormat | None) -> int | None:
    total = 0
    for f in fmts:
        if f is None:
            continue
        if not f.filesize:
            return None
        total += f.filesize
    return total
