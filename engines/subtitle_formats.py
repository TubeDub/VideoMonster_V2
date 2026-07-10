"""Parse and export subtitle formats: SRT, VTT, ASS, SSA, TXT."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass
class SubtitleSegment:
    index: int
    start_ms: int
    end_ms: int
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
VTT_TIME = re.compile(
    r"(\d{1,2}:)?(\d{2}):(\d{2})\.(\d{3})\s*-->\s*"
    r"(\d{1,2}:)?(\d{2}):(\d{2})\.(\d{3})"
)
ASS_DIALOGUE = re.compile(
    r"^Dialogue:\s*\d+,"
    r"(\d+):(\d{2}):(\d{2})\.(\d{2}),"
    r"(\d+):(\d{2}):(\d{2})\.(\d{2}),"
    r"[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,(.*)$",
    re.IGNORECASE,
)


def _hms_to_ms(h: int, m: int, s: int, frac_ms: int = 0) -> int:
    return ((h * 3600) + (m * 60) + s) * 1000 + frac_ms


def _parse_clock_parts(h_str: str | None, m_str: str, s_str: str, frac: str) -> int:
    h = int(h_str[:-1]) if h_str and h_str.endswith(":") else int(h_str or 0)
    m = int(m_str)
    s = int(s_str)
    frac = frac.ljust(3, "0")[:3]
    return _hms_to_ms(h, m, s, int(frac))


def _ms_to_srt(ms: int) -> str:
    ms = max(0, int(ms))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    frac = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{frac:03d}"


def _ms_to_vtt(ms: int) -> str:
    return _ms_to_srt(ms).replace(",", ".")


def parse_subtitles(raw: str, ext: str) -> list[SubtitleSegment]:
    ext = (ext or ".txt").lower()
    if not ext.startswith("."):
        ext = f".{ext}"

    if ext == ".vtt":
        return _parse_vtt(raw)
    if ext in (".ass", ".ssa"):
        return _parse_ass(raw)
    if ext == ".txt":
        return _parse_txt(raw)
    return _parse_srt(raw)


def _parse_srt(raw: str) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n").strip())
    idx = 1
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        time_line = None
        text_lines: list[str] = []
        for line in lines:
            if "-->" in line:
                time_line = line
            elif time_line is None and line.isdigit():
                continue
            elif time_line is not None:
                text_lines.append(re.sub(r"<[^>]+>", "", line))
        if not time_line or not text_lines:
            continue
        m = SRT_TIME.search(time_line)
        if not m:
            continue
        start_ms = _parse_clock_parts(None, m.group(1), m.group(2), m.group(4))
        end_ms = _parse_clock_parts(None, m.group(5), m.group(6), m.group(8))
        text = "\n".join(text_lines).strip()
        if not text:
            continue
        segments.append(SubtitleSegment(idx, start_ms, end_ms, text))
        idx += 1
    return segments


def _parse_vtt(raw: str) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    lines = raw.replace("\r\n", "\n").splitlines()
    idx = 1
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.upper().startswith("WEBVTT") or line.startswith("NOTE"):
            i += 1
            continue
        if "-->" not in line:
            i += 1
            continue
        m = VTT_TIME.search(line)
        if not m:
            i += 1
            continue
        start_ms = _parse_clock_parts(m.group(1), m.group(2), m.group(3), m.group(4))
        end_ms = _parse_clock_parts(m.group(5), m.group(6), m.group(7), m.group(8))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(re.sub(r"<[^>]+>", "", lines[i].strip()))
            i += 1
        text = "\n".join(text_lines).strip()
        if text:
            segments.append(SubtitleSegment(idx, start_ms, end_ms, text))
            idx += 1
    return segments


def _parse_ass(raw: str) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    idx = 1
    for line in raw.replace("\r\n", "\n").splitlines():
        m = ASS_DIALOGUE.match(line.strip())
        if not m:
            continue
        start_ms = _hms_to_ms(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)) * 10)
        end_ms = _hms_to_ms(int(m.group(5)), int(m.group(6)), int(m.group(7)), int(m.group(8)) * 10)
        text = m.group(9).replace("\\N", "\n").replace("{\\i0}", "").strip()
        text = re.sub(r"\{[^}]+\}", "", text).strip()
        if text:
            segments.append(SubtitleSegment(idx, start_ms, end_ms, text))
            idx += 1
    return segments


def _parse_txt(raw: str) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    idx = 1
    cursor = 0
    for line in raw.replace("\r\n", "\n").splitlines():
        text = line.strip()
        if not text:
            continue
        start_ms = cursor
        end_ms = cursor + 3000
        segments.append(SubtitleSegment(idx, start_ms, end_ms, text))
        idx += 1
        cursor = end_ms + 200
    return segments


def segments_to_text(segments: list[SubtitleSegment]) -> str:
    return "\n".join(seg.text for seg in segments)


def segments_to_timing_map(segments: list[SubtitleSegment]) -> list[dict[str, int]]:
    return [{"start": seg.start_ms, "end": seg.end_ms} for seg in segments]


def segments_from_payload(items: list[dict]) -> list[SubtitleSegment]:
    out: list[SubtitleSegment] = []
    for i, item in enumerate(items, 1):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        out.append(
            SubtitleSegment(
                index=int(item.get("index", i)),
                start_ms=int(item.get("start_ms", 0)),
                end_ms=int(item.get("end_ms", max(int(item.get("start_ms", 0)) + 3000, 3000))),
                text=text,
            )
        )
    return out


def export_srt(segments: list[SubtitleSegment]) -> str:
    blocks: list[str] = []
    for i, seg in enumerate(segments, 1):
        blocks.append(
            f"{i}\n"
            f"{_ms_to_srt(seg.start_ms)} --> {_ms_to_srt(seg.end_ms)}\n"
            f"{seg.text.strip()}\n"
        )
    return "\n".join(blocks).strip() + "\n"


def export_vtt(segments: list[SubtitleSegment]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_ms_to_vtt(seg.start_ms)} --> {_ms_to_vtt(seg.end_ms)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"
