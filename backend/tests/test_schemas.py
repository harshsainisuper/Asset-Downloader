"""Output selection and option validation."""

import pytest
from pydantic import ValidationError

from app.schemas import JobCreateRequest, Outputs, TranscriptOptions, VideoOptions

URL = "https://youtu.be/dQw4w9WgXcQ"


def test_at_least_one_output_required():
    with pytest.raises(ValidationError, match="Select at least one output"):
        Outputs()
    with pytest.raises(ValidationError):
        JobCreateRequest(url=URL, outputs={})


@pytest.mark.parametrize("kind", ["video", "audio", "thumbnail", "transcript"])
def test_any_single_output_is_valid(kind):
    outputs = Outputs(**{kind: {}})
    assert outputs.selected() == [kind]


def test_all_outputs_selected():
    outputs = Outputs(video={}, audio={}, thumbnail={}, transcript={})
    assert outputs.selected() == ["video", "audio", "thumbnail", "transcript"]


def test_null_means_not_selected():
    outputs = Outputs(video=None, audio={"format": "wav"})
    assert outputs.selected() == ["audio"]


def test_unknown_output_or_option_rejected():
    with pytest.raises(ValidationError):
        Outputs(video={}, playlist={})
    with pytest.raises(ValidationError):
        Outputs(video={"quality": 1080, "path": "/etc/passwd"})


@pytest.mark.parametrize("value,expected", [("1080", 1080), ("1080p", 1080), (720, 720), ("best", "best")])
def test_quality_coercion(value, expected):
    assert VideoOptions(quality=value).quality == expected


@pytest.mark.parametrize("value", [1081, "4k", "9999p", -1, None])
def test_invalid_quality(value):
    with pytest.raises(ValidationError):
        VideoOptions(quality=value)


def test_invalid_formats_rejected():
    with pytest.raises(ValidationError):
        VideoOptions(format="avi")
    with pytest.raises(ValidationError):
        Outputs(audio={"format": "flac"})
    with pytest.raises(ValidationError):
        Outputs(audio={"format": "mp3", "mp3_bitrate": 999})
    with pytest.raises(ValidationError):
        Outputs(thumbnail={"format": "gif"})


def test_transcript_formats():
    assert TranscriptOptions(formats=["srt", "srt", "txt"]).formats == ["srt", "txt"]
    with pytest.raises(ValidationError):
        TranscriptOptions(formats=[])
    with pytest.raises(ValidationError):
        TranscriptOptions(formats=["docx"])


@pytest.mark.parametrize("track_id", ["auto", "manual:en", "auto:en-orig", "manual:pt-BR"])
def test_valid_track_ids(track_id):
    assert TranscriptOptions(track_id=track_id).track_id == track_id


@pytest.mark.parametrize("track_id", ["../../etc", "manual:", "x:en", "manual:en;rm -rf"])
def test_invalid_track_ids(track_id):
    with pytest.raises(ValidationError):
        TranscriptOptions(track_id=track_id)
