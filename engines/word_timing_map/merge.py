"""Merge word maps when STT segments are combined."""

from __future__ import annotations

from typing import Any

from engines.word_timing_map.extract import build_segment_word_map, detect_pauses
from engines.word_timing_map.models import SegmentWordMap, WordToken


def _timing_start(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("start", 0))
    if isinstance(item, (list, tuple)) and len(item) >= 1:
        return int(item[0])
    return 0


def _timing_end(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("end", 0))
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[1])
    return 0


def merge_word_maps_with_segments(
    raw_segments: list[str],
    raw_timing: list[Any],
    merged_segments: list[str],
    merged_timing: list[Any],
    raw_word_maps: list[SegmentWordMap] | None = None,
) -> list[SegmentWordMap]:
    """
    Mirror segment_merger geometry: concatenate words from raw blocks
    that were merged into each output segment.
    """
    if not merged_segments or not merged_timing:
        return []

    if not raw_word_maps:
        from engines.word_timing_map.extract import build_segment_word_maps

        raw_word_maps = build_segment_word_maps(raw_segments, raw_timing)

    n_raw = min(len(raw_segments), len(raw_timing), len(raw_word_maps))
    if n_raw == 0:
        return build_segment_word_maps(merged_segments, merged_timing)

    # Map each raw index to merged index by matching start_ms
    raw_to_merged: dict[int, int] = {}
    mi = 0
    for ri in range(n_raw):
        rs = _timing_start(raw_timing[ri])
        while mi < len(merged_timing) and _timing_end(merged_timing[mi]) < rs:
            mi += 1
        if mi >= len(merged_timing):
            raw_to_merged[ri] = len(merged_timing) - 1
        else:
            raw_to_merged[ri] = mi

    buckets: dict[int, list[WordToken]] = {i: [] for i in range(len(merged_segments))}
    source_flags: dict[int, list[str]] = {i: [] for i in range(len(merged_segments))}
    for ri, wmap in enumerate(raw_word_maps[:n_raw]):
        bucket = raw_to_merged.get(ri, 0)
        buckets.setdefault(bucket, []).extend(wmap.words)
        source_flags.setdefault(bucket, []).append(wmap.timing_source)

    out: list[SegmentWordMap] = []
    for i, text in enumerate(merged_segments):
        start_ms = _timing_start(merged_timing[i]) if i < len(merged_timing) else 0
        end_ms = _timing_end(merged_timing[i]) if i < len(merged_timing) else start_ms
        words = buckets.get(i, [])
        flags = source_flags.get(i, [])
        ts = "real" if flags and all(f == "real" for f in flags) else "estimated"
        if not words and text.strip():
            out.append(
                build_segment_word_map(i, text, start_ms, end_ms, None, timing_source="estimated")
            )
        else:
            out.append(
                SegmentWordMap(
                    segment_index=i,
                    segment_start_ms=start_ms,
                    segment_end_ms=end_ms,
                    words=words,
                    pauses_ms=detect_pauses(words),
                    timing_source=ts if words else "estimated",
                )
            )
    return out
