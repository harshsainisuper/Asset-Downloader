"""Parsers for YouTube caption formats (json3, WebVTT, SRT, srv1/srv3/TTML XML)."""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import Segment

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]+")
_TIMING_RE = re.compile(
    r"^\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})"
)


class CaptionParseError(ValueError):
    pass


def parse_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(value)
    except ValueError as exc:
        raise CaptionParseError(f"bad timestamp {value!r}") from exc


def _clean_text(text: str) -> str:
    text = html.unescape(_TAG_RE.sub("", text))
    lines = [_WS_RE.sub(" ", line).strip() for line in text.replace("\r", "").split("\n")]
    return "\n".join(line for line in lines if line)


def parse_json3(text: str) -> list[Segment]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CaptionParseError("invalid json3") from exc
    segments: list[Segment] = []
    for event in data.get("events") or []:
        segs = event.get("segs")
        if not segs:
            continue
        content = _clean_text("".join(str(s.get("utf8", "")) for s in segs))
        if not content:
            continue
        start = (event.get("tStartMs") or 0) / 1000
        end = start + (event.get("dDurationMs") or 0) / 1000
        segments.append(Segment(start, end, content))
    return segments


def parse_webvtt_or_srt(text: str, rolling: bool = False) -> list[Segment]:
    """Parse WebVTT or SRT cues.

    With ``rolling=True`` (YouTube automatic captions), lines repeated from the
    previous cue and near-zero-length "snapshot" cues are dropped so words are not
    duplicated.
    """
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    segments: list[Segment] = []
    prev_lines: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").split("\n")
        idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if idx is None:
            continue  # header, NOTE, STYLE, REGION or garbage
        m = _TIMING_RE.match(lines[idx])
        if not m:
            continue
        start, end = parse_timestamp(m.group(1)), parse_timestamp(m.group(2))
        cue_lines = [_clean_text(line) for line in lines[idx + 1:]]
        cue_lines = [line for line in cue_lines if line]
        if rolling:
            if end - start < 0.05:
                prev_lines = cue_lines or prev_lines
                continue
            new_lines = [line for line in cue_lines if line not in prev_lines]
            prev_lines = cue_lines
            cue_lines = new_lines
        if cue_lines:
            segments.append(Segment(start, end, "\n".join(cue_lines)))
    return segments


def parse_timedtext_xml(text: str) -> list[Segment]:
    """srv1 (<text start dur>), srv3 (<p t d>) and basic TTML (<p begin end>)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise CaptionParseError("invalid caption XML") from exc
    segments: list[Segment] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        content = _clean_text("".join(el.itertext()))
        if not content:
            continue
        try:
            if tag == "text" and "start" in el.attrib:
                start = float(el.attrib["start"])
                end = start + float(el.attrib.get("dur", 0))
            elif tag == "p" and "t" in el.attrib:
                start = int(el.attrib["t"]) / 1000
                end = start + int(el.attrib.get("d", 0)) / 1000
            elif tag == "p" and "begin" in el.attrib:
                start = parse_timestamp(el.attrib["begin"].rstrip("s"))
                end = parse_timestamp(el.attrib.get("end", el.attrib["begin"]).rstrip("s"))
            else:
                continue
        except (ValueError, CaptionParseError):
            continue
        segments.append(Segment(start, end, content))
    return segments


def normalize_segments(segments: list[Segment], min_duration: float = 0.3) -> list[Segment]:
    """Sort, drop empties, and make cue times monotonic and non-overlapping."""
    cleaned = sorted((s for s in segments if s.text.strip()), key=lambda s: (s.start, s.end))
    result: list[Segment] = []
    for i, seg in enumerate(cleaned):
        start = max(0.0, seg.start)
        end = seg.end
        nxt = cleaned[i + 1].start if i + 1 < len(cleaned) else None
        if nxt is not None and end > nxt and nxt > start:
            end = nxt
        if end <= start:
            end = start + min_duration if nxt is None else max(min(nxt, start + 2.0), start + 0.01)
        if result and result[-1].text == seg.text and abs(result[-1].end - start) < 0.05:
            result[-1].end = end  # merge exact continuation duplicates
            continue
        result.append(Segment(round(start, 3), round(end, 3), seg.text.strip()))
    return result


def parse_caption_file(path: Path, rolling: bool = False) -> list[Segment]:
    text = path.read_text(encoding="utf-8", errors="replace")
    ext = path.suffix.lower().lstrip(".")
    if ext == "json3":
        segments = parse_json3(text)
    elif ext in ("vtt", "srt"):
        segments = parse_webvtt_or_srt(text, rolling=rolling)
    elif ext in ("srv1", "srv2", "srv3", "ttml", "xml"):
        segments = parse_timedtext_xml(text)
    else:
        stripped = text.lstrip()
        if stripped.startswith("{"):
            segments = parse_json3(text)
        elif stripped.startswith("<"):
            segments = parse_timedtext_xml(text)
        else:
            segments = parse_webvtt_or_srt(text, rolling=rolling)
    return normalize_segments(segments)
