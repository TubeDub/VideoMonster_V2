"""Phase 1 — build and persist Word Timing Maps through the dub pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engines.word_timing_map.extract import (
    attach_words_to_timing_map,
    build_segment_word_maps,
    detect_pauses,
    proportional_word_split,
)
from engines.word_timing_map.merge import merge_word_maps_with_segments
from engines.word_timing_map.models import SegmentWordMap, WordToken

logger = logging.getLogger("tubedub.word_timing_map")


def _timing_bounds(item: Any) -> tuple[int, int]:
    if isinstance(item, dict):
        return int(item.get("start", 0)), int(item.get("end", 0))
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[0]), int(item[1])
    return 0, 0


def _has_embedded_words(timing_map: list[Any]) -> bool:
    return any(
        isinstance(t, dict) and t.get("words") for t in timing_map
    )


def build_raw_word_maps(
    segments: list[str],
    timing_map: list[Any],
    *,
    timing_source: str | None = None,
) -> list[SegmentWordMap]:
    """
    Build word maps for raw STT / preloaded segments.
    Uses Whisper words when present; otherwise Approximate Word Timing (proportional).
    """
    if not segments:
        return []

    source = timing_source
    if source is None:
        source = "real" if _has_embedded_words(timing_map) else "estimated"

    maps = build_segment_word_maps(segments, timing_map)
    for i, swm in enumerate(maps):
        if not swm.words and i < len(segments) and i < len(timing_map):
            start_ms, end_ms = _timing_bounds(timing_map[i])
            swm.words = proportional_word_split(segments[i], start_ms, end_ms)
            swm.pauses_ms = detect_pauses(swm.words)
            swm.timing_source = "estimated"
        else:
            swm.timing_source = source if swm.words else "estimated"
    return maps


def build_merged_word_maps(
    raw_segments: list[str],
    raw_timing: list[Any],
    merged_segments: list[str],
    merged_timing: list[Any],
    *,
    raw_word_maps: list[SegmentWordMap] | None = None,
) -> list[SegmentWordMap]:
    """Merge word maps when segment_merger combines blocks — no words lost."""
    raw_maps = raw_word_maps or build_raw_word_maps(raw_segments, raw_timing)
    merged = merge_word_maps_with_segments(
        raw_segments,
        raw_timing,
        merged_segments,
        merged_timing,
        raw_maps,
    )
    for swm in merged:
        if not swm.words and swm.segment_start_ms < swm.segment_end_ms:
            idx = swm.segment_index
            text = merged_segments[idx] if idx < len(merged_segments) else ""
            swm.words = proportional_word_split(
                text, swm.segment_start_ms, swm.segment_end_ms
            )
            from engines.word_timing_map.extract import detect_pauses

            swm.pauses_ms = detect_pauses(swm.words)
            swm.timing_source = "estimated"
        elif any(w.confidence <= 0.55 for w in swm.words):
            swm.timing_source = "estimated"
        else:
            swm.timing_source = getattr(swm, "timing_source", None) or "real"
    return merged


def sync_timing_map_words(
    timing_map: list[Any],
    word_maps: list[SegmentWordMap],
) -> list[dict[str, Any]]:
    """Embed words + timing_source into timing_map for cache / backup."""
    enriched = attach_words_to_timing_map(
        [dict(t) if isinstance(t, dict) else {"start": _timing_bounds(t)[0], "end": _timing_bounds(t)[1]} for t in timing_map],
        word_maps,
    )
    for i, entry in enumerate(enriched):
        if i < len(word_maps):
            entry["timing_source"] = word_maps[i].timing_source
    return enriched


def attach_word_maps_to_segments_data(
    segments_data: list[dict[str, Any]],
    word_maps: list[SegmentWordMap],
) -> list[dict[str, Any]]:
    """Add source_word_map to each segment dict (Phase 1 persist)."""
    for i, seg in enumerate(segments_data):
        if i < len(word_maps):
            seg["source_word_map"] = word_maps[i].to_dict()
        elif "source_word_map" not in seg:
            seg["source_word_map"] = None
    return segments_data


def word_maps_from_task_info(info: dict[str, Any]) -> list[SegmentWordMap]:
    raw = info.get("source_word_maps") or []
    maps: list[SegmentWordMap] = []
    for item in raw:
        if isinstance(item, dict):
            maps.append(SegmentWordMap.from_dict(item))
    if maps:
        return maps
    for seg in info.get("segments_data") or []:
        swm = seg.get("source_word_map")
        if isinstance(swm, dict):
            maps.append(SegmentWordMap.from_dict(swm))
    return maps


def persist_task_word_maps(
    info: dict[str, Any],
    word_maps: list[SegmentWordMap],
    *,
    timing_map: list[Any] | None = None,
) -> None:
    """Store word maps on task info + optional timing_map sync."""
    info["source_word_maps"] = [m.to_dict() for m in word_maps]
    real_count = sum(1 for m in word_maps if m.timing_source == "real")
    info["word_timing_meta"] = {
        "segments": len(word_maps),
        "words_total": sum(len(m.words) for m in word_maps),
        "real_segments": real_count,
        "estimated_segments": len(word_maps) - real_count,
    }
    if timing_map is not None:
        synced = sync_timing_map_words(timing_map, word_maps)
        info["timing_map_backup"] = synced


def save_word_timing_dev_report(
    app_dir: Path,
    word_maps: list[SegmentWordMap],
    *,
    task_id: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    out_dir = app_dir / "output" / "dev" / "word_timing_map"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"wtm_{task_id}.json" if task_id else "wtm_latest.json"
    path = out_dir / name
    payload = {
        "task_id": task_id,
        "segments": [m.to_dict() for m in word_maps],
        "meta": extra or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = out_dir / "wtm_latest.json"
    if path != latest:
        latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    logger.info("[WTM] dev report %s (%d segments)", path, len(word_maps))
    return str(path)
