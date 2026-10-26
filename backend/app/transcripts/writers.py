"""Render transcripts as TXT, SRT or WebVTT."""

from __future__ import annotations

from .models import Segment, Transcript, TranscriptFormat


def format_timestamp(seconds: float, decimal_sep: str = ",") -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{decimal_sep}{ms:03d}"


def to_srt(segments: list[Segment]) -> str:
    blocks = []
    for i, seg in enumerate(segments, start=1):
        text = seg.text.replace("-->", "->")
        blocks.append(f"{i}\n{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}\n{text}\n")
    return "\n".join(blocks)


def to_vtt(segments: list[Segment], note: str | None = None) -> str:
    out = ["WEBVTT", ""]
    if note:
        out += ["NOTE", note.replace("-->", "->"), ""]
    for seg in segments:
        text = seg.text.replace("-->", "->")
        out += [f"{format_timestamp(seg.start, '.')} --> {format_timestamp(seg.end, '.')}", text, ""]
    return "\n".join(out)


def to_txt(segments: list[Segment], header: str | None = None, paragraph_gap: float = 2.0) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    prev_end: float | None = None
    for seg in segments:
        text = " ".join(seg.text.split())
        if not text:
            continue
        long_para = sum(len(t) for t in current) > 600 and current and current[-1].endswith((".", "?", "!"))
        if current and ((prev_end is not None and seg.start - prev_end >= paragraph_gap) or long_para):
            paragraphs.append(" ".join(current))
            current = []
        current.append(text)
        prev_end = seg.end
    if current:
        paragraphs.append(" ".join(current))
    body = "\n\n".join(paragraphs)
    return (f"{header}\n\n{body}\n" if header else f"{body}\n")


def render(transcript: Transcript, fmt: TranscriptFormat, title: str) -> str:
    header = f"{title}\nSource: {transcript.description}"
    if fmt == "srt":
        return to_srt(transcript.segments)
    if fmt == "vtt":
        return to_vtt(transcript.segments, note=header)
    if fmt == "txt":
        return to_txt(transcript.segments, header=header)
    raise ValueError(f"unsupported transcript format {fmt}")
