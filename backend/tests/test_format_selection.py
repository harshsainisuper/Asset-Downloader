import pytest

from app.errors import FormatUnavailableError
from app.media.format_selection import available_qualities, plan_audio, plan_video, quality_tier
from app.media.models import MediaFormat

from .conftest import youtube_like_formats


def test_available_qualities_only_lists_real_tiers():
    qualities = available_qualities(youtube_like_formats())
    heights = [q.height for q in qualities]
    assert heights == [2160, 1440, 1080, 720, 360]
    assert 480 not in heights
    by_height = {q.height: q for q in qualities}
    assert by_height[1080].mp4 == "h264"
    assert by_height[2160].mp4 == "av1"
    assert by_height[1440].mp4 == "transcode"
    assert by_height[1440].webm == "native"


def test_vertical_short_tier_uses_short_side():
    f = MediaFormat("1", "mp4", vcodec="avc1", width=1080, height=1920)
    assert quality_tier(f) == 1080
    f2 = MediaFormat("2", "mp4", vcodec="avc1", width=1080, height=1920, format_note="1080p60")
    assert quality_tier(f2) == 1080


def test_plan_mp4_1080_uses_h264_and_aac_without_reencoding():
    plan = plan_video(youtube_like_formats(), 1080, "mp4")
    assert plan.video.format_id == "137"
    assert plan.audio is not None and plan.audio.format.format_id == "140"
    assert plan.video_action == "copy" and plan.audio_action == "copy"
    assert plan.video_codec_label == "H.264" and plan.audio_codec_label == "AAC"


def test_plan_webm_1080_uses_vp9_and_opus():
    plan = plan_video(youtube_like_formats(), 1080, "webm")
    assert plan.video.format_id == "248"
    assert plan.audio is not None and plan.audio.format.format_id == "251"  # not the DRC variant
    assert plan.audio_action == "copy"


def test_best_picks_highest_tier():
    assert plan_video(youtube_like_formats(), "best", "webm").tier == 2160
    assert plan_video(youtube_like_formats(), "best", "mp4").tier == 2160


def test_4k_mp4_uses_av1_or_transcodes_in_compat_mode():
    plan = plan_video(youtube_like_formats(), 2160, "mp4")
    assert plan.video.format_id == "401" and plan.video_action == "copy"
    assert any("AV1" in n for n in plan.notes)
    compat = plan_video(youtube_like_formats(), 2160, "mp4", h264_compat=True)
    assert compat.video_action == "transcode_h264"
    assert compat.video_codec_label == "H.264"


def test_1440_mp4_requires_transcode_and_says_so():
    plan = plan_video(youtube_like_formats(), 1440, "mp4")
    assert plan.video.format_id == "271"
    assert plan.video_action == "transcode_h264"
    assert any("re-encoded" in n for n in plan.notes)


def test_unavailable_resolution_is_reported():
    with pytest.raises(FormatUnavailableError, match="480p is not available"):
        plan_video(youtube_like_formats(), 480, "mp4")


def test_webm_unavailable_when_only_h264():
    formats = [f for f in youtube_like_formats() if not (f.vcodec or "").startswith(("vp9", "av01"))]
    with pytest.raises(FormatUnavailableError, match="WebM"):
        plan_video(formats, 1080, "webm")


def test_invalid_container_rejected():
    with pytest.raises(FormatUnavailableError):
        plan_video(youtube_like_formats(), 1080, "avi")  # type: ignore[arg-type]


def test_webm_with_only_aac_audio_transcodes_audio():
    formats = [f for f in youtube_like_formats() if f.format_id not in ("251", "251-drc")]
    plan = plan_video(formats, 720, "webm")
    assert plan.audio_action == "opus"


def test_muxed_only_video_uses_embedded_audio():
    formats = [MediaFormat("18", "mp4", vcodec="avc1.42001E", acodec="mp4a.40.2", width=640, height=360,
                           format_note="360p")]
    plan = plan_video(formats, "best", "mp4")
    assert plan.audio is not None and plan.audio.embedded_in_video
    assert plan.audio.format.format_id == "18"


def test_silent_video_has_no_audio_plan():
    formats = [MediaFormat("137", "mp4", vcodec="avc1", width=1920, height=1080, format_note="1080p")]
    plan = plan_video(formats, 1080, "mp4")
    assert plan.audio is None and plan.audio_action == "none"


def test_no_video_formats():
    formats = [MediaFormat("140", "m4a", acodec="mp4a.40.2", abr=128)]
    with pytest.raises(FormatUnavailableError):
        plan_video(formats, "best", "mp4")


def test_plan_audio_m4a_copies_aac():
    plan = plan_audio(youtube_like_formats(), "m4a")
    assert plan.source.format.format_id == "140"
    assert plan.action == "copy"


def test_plan_audio_mp3_uses_best_source():
    plan = plan_audio(youtube_like_formats(), "mp3", 192)
    assert plan.source.format.format_id == "251"
    assert plan.bitrate_kbps == 192 and plan.action == "transcode"


def test_plan_audio_wav_explains_lossy_source():
    plan = plan_audio(youtube_like_formats(), "wav")
    assert any("does not restore" in n for n in plan.notes)


def test_plan_audio_rejects_unknown_format_and_bitrate():
    with pytest.raises(FormatUnavailableError):
        plan_audio(youtube_like_formats(), "flac")  # type: ignore[arg-type]
    with pytest.raises(FormatUnavailableError):
        plan_audio(youtube_like_formats(), "mp3", 999)


def test_audio_prefers_original_language_track():
    formats = [
        MediaFormat("251-dub", "webm", acodec="opus", abr=160, language="de", language_preference=-1),
        MediaFormat("251-orig", "webm", acodec="opus", abr=130, language="en", language_preference=10),
    ]
    assert plan_audio(formats, "mp3").source.format.format_id == "251-orig"


def test_audio_without_any_audio_track():
    formats = [MediaFormat("137", "mp4", vcodec="avc1", width=1920, height=1080, format_note="1080p")]
    with pytest.raises(FormatUnavailableError, match="no audio"):
        plan_audio(formats, "mp3")
