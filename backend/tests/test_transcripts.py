from app.media.models import CaptionTrack
from app.transcripts.models import Segment, Transcript
from app.transcripts.parsers import (
    normalize_segments,
    parse_json3,
    parse_timedtext_xml,
    parse_webvtt_or_srt,
)
from app.transcripts.tracks import default_track
from app.transcripts.writers import format_timestamp, render, to_srt, to_txt, to_vtt

from .conftest import SAMPLE_JSON3, make_info


def test_parse_json3_skips_newline_events_and_unescapes():
    segs = parse_json3(SAMPLE_JSON3)
    assert [s.text for s in segs] == ["Hello and welcome.", "Second line & more"]
    assert segs[0].start == 0 and segs[0].end == 1.5
    assert segs[1].start == 4.0 and segs[1].end == 6.0


def test_parse_vtt_with_tags_and_settings():
    vtt = """WEBVTT
Kind: captions
Language: en

NOTE this is a comment

00:00:01.000 --> 00:00:02.500 align:start position:0%
<c>Hello</c> <00:00:01.500><c>world</c>

00:00:03.000 --> 00:00:04.000
Second &amp; cue
"""
    segs = parse_webvtt_or_srt(vtt)
    assert [(s.start, s.end, s.text) for s in segs] == [(1.0, 2.5, "Hello world"), (3.0, 4.0, "Second & cue")]


def test_parse_auto_vtt_removes_rolling_duplicates():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
first line

00:00:02.000 --> 00:00:02.010
first line

00:00:02.010 --> 00:00:04.000
first line
second line

00:00:04.000 --> 00:00:04.010
second line

00:00:04.010 --> 00:00:06.000
second line
third line
"""
    segs = parse_webvtt_or_srt(vtt, rolling=True)
    assert [s.text for s in segs] == ["first line", "second line", "third line"]


def test_parse_srt():
    srt = "1\n00:00:01,000 --> 00:00:02,000\nHi\n\n2\n00:00:02,500 --> 00:00:03,000\nThere\n"
    assert [(s.start, s.text) for s in parse_webvtt_or_srt(srt)] == [(1.0, "Hi"), (2.5, "There")]


def test_parse_srv3_xml():
    xml = '<timedtext><body><p t="1000" d="1500">Hello</p><p t="3000" d="1000">World</p></body></timedtext>'
    segs = parse_timedtext_xml(xml)
    assert [(s.start, s.end, s.text) for s in segs] == [(1.0, 2.5, "Hello"), (3.0, 4.0, "World")]


def test_normalize_fixes_overlaps_and_zero_length():
    segs = normalize_segments([Segment(0, 5, "a"), Segment(2, 3, "b"), Segment(4, 4, "c"), Segment(6, 7, " ")])
    assert [(s.start, s.end, s.text) for s in segs] == [(0, 2, "a"), (2, 3, "b"), (4, 4.3, "c")]


def test_timestamps():
    assert format_timestamp(3725.5) == "01:02:05,500"
    assert format_timestamp(0.0004, ".") == "00:00:00.000"


def test_srt_and_vtt_output():
    segs = [Segment(0, 1.25, "One"), Segment(2, 3, "Two\nlines")]
    assert to_srt(segs) == "1\n00:00:00,000 --> 00:00:01,250\nOne\n\n2\n00:00:02,000 --> 00:00:03,000\nTwo\nlines\n"
    vtt = to_vtt(segs, note="Source: test")
    assert vtt.startswith("WEBVTT\n\nNOTE\nSource: test\n\n00:00:00.000 --> 00:00:01.250\nOne\n")


def test_txt_groups_paragraphs_on_pauses():
    segs = [Segment(0, 1, "Hello"), Segment(1, 2, "there."), Segment(10, 11, "New topic.")]
    assert to_txt(segs) == "Hello there.\n\nNew topic.\n"


def test_render_labels_ai_transcripts():
    t = Transcript([Segment(0, 1, "x")], origin="ai_transcript", language="en", model="whisper-1")
    out = render(t, "txt", "Title")
    assert "AI-generated transcript" in out and "whisper-1" in out
    t2 = Transcript([Segment(0, 1, "x")], origin="manual_captions", language="en", language_name="English")
    assert "Creator-provided captions" in render(t2, "vtt", "Title")


def test_default_track_preference():
    auto = CaptionTrack("en-orig", "en", "English (Original)", "auto", True)
    manual_fr = CaptionTrack("fr", "fr", "French", "manual", False)
    manual_en = CaptionTrack("en", "en", "English", "manual", True)
    assert default_track(make_info(captions=[auto, manual_fr, manual_en])) is manual_en
    assert default_track(make_info(captions=[auto, manual_fr])) is manual_fr
    assert default_track(make_info(captions=[auto])) is auto
    assert default_track(make_info(captions=[])) is None
