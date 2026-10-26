"""Caption track selection."""

from __future__ import annotations

from ..errors import AppError
from ..media.models import CaptionTrack, VideoInfo


def default_track(info: VideoInfo) -> CaptionTrack | None:
    """Prefer original-language creator captions, then any creator captions,
    then YouTube's automatic captions in the original language."""
    manual = [c for c in info.captions if c.kind == "manual"]
    auto = [c for c in info.captions if c.kind == "auto"]
    for pool in (
        [c for c in manual if c.is_original],
        manual,
        [c for c in auto if c.is_original],
        auto,
    ):
        if pool:
            return pool[0]
    return None


def resolve_track(info: VideoInfo, track_id: str | None) -> CaptionTrack | None:
    if not track_id or track_id == "auto":
        return default_track(info)
    track = info.caption_by_id(track_id)
    if track is None:
        raise AppError("caption_track_unavailable", "The selected caption language is not available.", 422)
    return track
