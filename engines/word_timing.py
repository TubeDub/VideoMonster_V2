"""WordTimingMap facade — Whisper word timestamps + heuristic fallback (TZ §4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from engines.core.pipeline_contracts import WordTiming


@dataclass
class WordTimingMap:
    """Per-segment word timing map."""

    segment_index: int
    text: str
    words: list[WordTiming] = field(default_factory=list)
    timing_source: str = "estimated"
    segment_start_ms: int = 0
    segment_end_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_index": self.segment_index,
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
            "timing_source": self.timing_source,
            "segment_start_ms": self.segment_start_ms,
            "segment_end_ms": self.segment_end_ms,
        }


def _parse_timing(item: Any) -> tuple[int, int]:
    if isinstance(item, dict):
        return int(item.get("start", item.get("start_ms", 0))), int(
            item.get("end", item.get("end_ms", 0))
        )
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[0]), int(item[1])
    return 0, 0


def _map_from_segment_word_map(sm: Any, text: str) -> WordTimingMap:
    return WordTimingMap(
        segment_index=sm.segment_index,
        text=text,
        words=[
            WordTiming(
                text=w.text,
                start_ms=w.start_ms,
                end_ms=w.end_ms,
                confidence=w.confidence,
                source=sm.timing_source,
            )
            for w in sm.words
        ],
        timing_source=sm.timing_source,
        segment_start_ms=sm.segment_start_ms,
        segment_end_ms=sm.segment_end_ms,
    )


def build_from_whisper(
    segments: Sequence[str],
    timing_map: Sequence[Any],
) -> list[WordTimingMap]:
    """
    Build word maps from STT output.
    Uses embedded Whisper word timestamps when present; otherwise syllable/heuristic split.
    """
    from engines.word_timing_map.pipeline import build_raw_word_maps

    raw_maps = build_raw_word_maps(list(segments), list(timing_map))
    out: list[WordTimingMap] = []
    for i, sm in enumerate(raw_maps):
        text = str(segments[i] if i < len(segments) else "")
        out.append(_map_from_segment_word_map(sm, text))
    return out


def build_merged_maps(
    raw_segments: Sequence[str],
    raw_timing: Sequence[Any],
    merged_segments: Sequence[str],
    merged_timing: Sequence[Any],
    raw_maps: list[WordTimingMap] | None = None,
) -> list[WordTimingMap]:
    from engines.word_timing_map.models import SegmentWordMap, WordToken
    from engines.word_timing_map.pipeline import build_merged_word_maps, build_raw_word_maps

    if raw_maps:
        seg_maps = [
            SegmentWordMap(
                segment_index=m.segment_index,
                segment_start_ms=m.segment_start_ms,
                segment_end_ms=m.segment_end_ms,
                words=[
                    WordToken(
                        text=w.text,
                        start_ms=w.start_ms,
                        end_ms=w.end_ms,
                        confidence=w.confidence,
                    )
                    for w in m.words
                ],
                timing_source=m.timing_source,
            )
            for m in raw_maps
        ]
    else:
        seg_maps = build_raw_word_maps(list(raw_segments), list(raw_timing))

    merged = build_merged_word_maps(
        list(raw_segments),
        list(raw_timing),
        list(merged_segments),
        list(merged_timing),
        raw_word_maps=seg_maps,
    )
    out: list[WordTimingMap] = []
    for i, sm in enumerate(merged):
        text = str(merged_segments[i] if i < len(merged_segments) else "")
        out.append(_map_from_segment_word_map(sm, text))
    return out


def sync_timing_map(
    timing_map: list[Any],
    word_maps: list[WordTimingMap],
) -> list[Any]:
    from engines.word_timing_map.models import SegmentWordMap, WordToken
    from engines.word_timing_map.pipeline import sync_timing_map_words

    seg_maps = [
        SegmentWordMap(
            segment_index=m.segment_index,
            segment_start_ms=m.segment_start_ms,
            segment_end_ms=m.segment_end_ms,
            words=[
                WordToken(
                    text=w.text,
                    start_ms=w.start_ms,
                    end_ms=w.end_ms,
                    confidence=w.confidence,
                )
                for w in m.words
            ],
            timing_source=m.timing_source,
        )
        for m in word_maps
    ]
    return sync_timing_map_words(timing_map, seg_maps)


def persist_to_task_info(
    info: dict[str, Any],
    word_maps: list[WordTimingMap],
    timing_map: list[Any] | None = None,
) -> None:
    from engines.word_timing_map.models import SegmentWordMap, WordToken
    from engines.word_timing_map.pipeline import persist_task_word_maps

    seg_maps = [
        SegmentWordMap(
            segment_index=m.segment_index,
            segment_start_ms=m.segment_start_ms,
            segment_end_ms=m.segment_end_ms,
            words=[
                WordToken(
                    text=w.text,
                    start_ms=w.start_ms,
                    end_ms=w.end_ms,
                    confidence=w.confidence,
                )
                for w in m.words
            ],
            timing_source=m.timing_source,
        )
        for m in word_maps
    ]
    persist_task_word_maps(info, seg_maps, timing_map=timing_map)
