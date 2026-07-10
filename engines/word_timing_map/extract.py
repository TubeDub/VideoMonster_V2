"""Extract and build word timing maps from STT output."""

from __future__ import annotations

import re
from typing import Any

from engines.word_timing_map.config import MIN_WORD_GAP_MS
from engines.word_timing_map.models import PauseGap, SegmentWordMap, WordToken

_WORD_RE = re.compile(r"\S+", re.UNICODE)


def proportional_word_split(
    text: str,
    start_ms: int,
    end_ms: int,
) -> list[WordToken]:
    """Fallback when Whisper word timestamps are unavailable."""
    tokens = _WORD_RE.findall(str(text or "").strip())
    if not tokens:
        return []
    span = max(1, end_ms - start_ms)
    weights = [max(1, len(t)) for t in tokens]
    total = sum(weights)
    out: list[WordToken] = []
    cursor = start_ms
    for i, tok in enumerate(tokens):
        if i == len(tokens) - 1:
            end = end_ms
        else:
            share = int(span * weights[i] / total)
            end = min(end_ms, cursor + max(40, share))
        out.append(WordToken(text=tok, start_ms=cursor, end_ms=end, confidence=0.5))
        cursor = end
    return out


def words_from_faster_whisper_segment(seg: Any) -> list[WordToken]:
    out: list[WordToken] = []
    for w in getattr(seg, "words", None) or []:
        text = str(getattr(w, "word", "") or "").strip()
        if not text:
            continue
        start = int(round(float(getattr(w, "start", 0)) * 1000))
        end = int(round(float(getattr(w, "end", 0)) * 1000))
        prob = float(getattr(w, "probability", 1.0) or 1.0)
        out.append(WordToken(text=text, start_ms=start, end_ms=max(start + 20, end), confidence=prob))
    return out


def words_from_openai_whisper_segment(seg: dict[str, Any]) -> list[WordToken]:
    out: list[WordToken] = []
    for w in seg.get("words") or []:
        text = str(w.get("word") or "").strip()
        if not text:
            continue
        start = int(round(float(w.get("start", 0)) * 1000))
        end = int(round(float(w.get("end", 0)) * 1000))
        prob = float(w.get("probability", 1.0) or 1.0)
        out.append(WordToken(text=text, start_ms=start, end_ms=max(start + 20, end), confidence=prob))
    return out


def detect_pauses(words: list[WordToken], *, min_gap_ms: int = MIN_WORD_GAP_MS) -> list[PauseGap]:
    pauses: list[PauseGap] = []
    for i in range(len(words) - 1):
        gap = words[i + 1].start_ms - words[i].end_ms
        if gap >= min_gap_ms:
            pauses.append(PauseGap(after_word_index=i, duration_ms=gap, type="natural"))
    return pauses


def build_segment_word_map(
    index: int,
    text: str,
    start_ms: int,
    end_ms: int,
    words: list[WordToken] | None = None,
    *,
    timing_source: str = "estimated",
) -> SegmentWordMap:
    wlist = list(words or [])
    src = timing_source
    if not wlist and text.strip():
        wlist = proportional_word_split(text, start_ms, end_ms)
        src = "estimated"
    elif wlist and src == "estimated" and all(w.confidence > 0.55 for w in wlist):
        src = "real"
    return SegmentWordMap(
        segment_index=index,
        segment_start_ms=start_ms,
        segment_end_ms=end_ms,
        words=wlist,
        pauses_ms=detect_pauses(wlist),
        timing_source=src,
    )


def build_segment_word_maps(
    segments: list[str],
    timing_map: list[Any],
    *,
    raw_words_per_segment: list[list[WordToken]] | None = None,
) -> list[SegmentWordMap]:
    """Build SegmentWordMap for each segment from timing_map entries."""
    out: list[SegmentWordMap] = []
    n = min(len(segments), len(timing_map))
    raw = raw_words_per_segment or []

    for i in range(n):
        timing = timing_map[i]
        if isinstance(timing, dict):
            start_ms = int(timing.get("start", 0))
            end_ms = int(timing.get("end", 0))
            embedded = timing.get("words")
        elif isinstance(timing, (list, tuple)) and len(timing) >= 2:
            start_ms, end_ms = int(timing[0]), int(timing[1])
            embedded = None
        else:
            start_ms, end_ms = 0, 0
            embedded = None

        words: list[WordToken] = []
        if i < len(raw) and raw[i]:
            words = raw[i]
        elif embedded:
            words = [WordToken.from_dict(w) if isinstance(w, dict) else w for w in embedded]

        text = str(segments[i] or "").strip()
        src = "real" if words else "estimated"
        out.append(build_segment_word_map(i, text, start_ms, end_ms, words, timing_source=src))

    return out


def extract_words_from_timing_map(timing_map: list[Any]) -> list[list[WordToken]]:
    """Pull word lists from timing_map dict entries."""
    result: list[list[WordToken]] = []
    for item in timing_map:
        if isinstance(item, dict) and item.get("words"):
            result.append([WordToken.from_dict(w) for w in item["words"]])
        else:
            result.append([])
    return result


def attach_words_to_timing_map(
    timing_map: list[dict[str, Any]],
    word_maps: list[SegmentWordMap],
) -> list[dict[str, Any]]:
    """Embed serialized words into timing_map for cache/persistence."""
    out: list[dict[str, Any]] = []
    for i, timing in enumerate(timing_map):
        entry = dict(timing) if isinstance(timing, dict) else {"start": 0, "end": 0}
        if i < len(word_maps) and word_maps[i].words:
            entry["words"] = [w.to_dict() for w in word_maps[i].words]
            entry["timing_source"] = word_maps[i].timing_source
        out.append(entry)
    return out
