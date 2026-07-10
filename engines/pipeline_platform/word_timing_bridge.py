"""Word Timing Map bridge — Stage 4. Map survives entire pipeline."""

from __future__ import annotations

from typing import Any


def merge_word_timings_on_fewer_words(
    words: list[dict[str, Any]],
    new_word_count: int,
) -> list[dict[str, Any]]:
    """If translated text has fewer words — merge adjacent timings."""
    if new_word_count <= 0 or not words:
        return []
    if new_word_count >= len(words):
        return redistribute_word_timings(words, new_word_count)
    out: list[dict[str, Any]] = []
    ratio = len(words) / max(new_word_count, 1)
    for i in range(new_word_count):
        a = int(i * ratio)
        b = min(len(words), int((i + 1) * ratio))
        if a >= b:
            b = min(len(words), a + 1)
        chunk = words[a:b]
        if not chunk:
            continue
        out.append(
            {
                "word": f"merged_{i}",
                "start_ms": int(chunk[0].get("start_ms", 0)),
                "end_ms": int(chunk[-1].get("end_ms", 0)),
                "duration_ms": int(chunk[-1].get("end_ms", 0)) - int(chunk[0].get("start_ms", 0)),
                "confidence": sum(float(w.get("confidence", 0)) for w in chunk) / len(chunk),
                "position": i,
                "merged_from": len(chunk),
            }
        )
    return out


def redistribute_word_timings(
    words: list[dict[str, Any]],
    new_word_count: int,
) -> list[dict[str, Any]]:
    """If more words than timings — split proportionally."""
    if not words or new_word_count <= len(words):
        return [dict(w) for w in words]
    if len(words) == 1:
        w = words[0]
        start = int(w.get("start_ms", 0))
        end = int(w.get("end_ms", start))
        span = max(end - start, 1)
        step = span / new_word_count
        return [
            {
                "word": f"split_{i}",
                "start_ms": int(start + i * step),
                "end_ms": int(start + (i + 1) * step),
                "duration_ms": int(step),
                "confidence": float(w.get("confidence", 0)),
                "position": i,
            }
            for i in range(new_word_count)
        ]
    return merge_word_timings_on_fewer_words(words, new_word_count)


def attach_wtm_to_envelope(envelope: Any, wtm: dict[str, Any]) -> None:
    envelope.word_timing_map = dict(wtm or {})


def wtm_from_segment_info(info: dict[str, Any], index: int) -> dict[str, Any]:
    maps = info.get("source_word_maps") or info.get("word_maps") or []
    if index < len(maps):
        row = maps[index]
        if isinstance(row, dict):
            return row
        if hasattr(row, "to_dict"):
            return row.to_dict()
    return {}
