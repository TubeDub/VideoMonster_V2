"""Prev/next segment context for Director analysis."""

from __future__ import annotations

from typing import Any


def _segment_text(seg: dict[str, Any] | None) -> str:
    if not seg:
        return ""
    return str(seg.get("text") or "").strip()


def build_context_window(
    segments: list[dict[str, Any]],
    index: int,
    *,
    window: int = 2,
) -> dict[str, Any]:
    """Return prev/next texts and neighbor brief hints for segment at index."""
    prev_texts: list[str] = []
    next_texts: list[str] = []
    for offset in range(window, 0, -1):
        j = index - offset
        if 0 <= j < len(segments):
            t = _segment_text(segments[j])
            if t:
                prev_texts.append(t)
    for offset in range(1, window + 1):
        j = index + offset
        if 0 <= j < len(segments):
            t = _segment_text(segments[j])
            if t:
                next_texts.append(t)

    prev_seg = segments[index - 1] if index > 0 else None
    next_seg = segments[index + 1] if index + 1 < len(segments) else None

    return {
        "prev_texts": prev_texts,
        "next_texts": next_texts,
        "prev_text": _segment_text(prev_seg),
        "next_text": _segment_text(next_seg),
        "prev_emotion": (prev_seg or {}).get("creative_brief", {}).get("emotion")
        if isinstance((prev_seg or {}).get("creative_brief"), dict)
        else None,
        "position": index,
        "total": len(segments),
    }


__all__ = ["build_context_window"]
