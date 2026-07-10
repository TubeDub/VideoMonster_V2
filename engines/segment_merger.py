"""
Объединение микро-сегментов Whisper в безопасные блоки для перевода и TTS.
Снижает atempo>1.3 и обрезку речи в timing_fit.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Sequence

logger = logging.getLogger("tubedub.engines.segment_merger")

MIN_SAFE_SEGMENT_MS = 4500
MAX_MERGED_SPAN_MS = 14000
MAX_GAP_MS = 1200
SHORT_SEGMENT_MS = 1800


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


def _segment_duration_ms(timing_map: Sequence[Any], idx: int) -> int:
    if not timing_map or idx >= len(timing_map):
        return 0
    return max(0, _timing_end(timing_map[idx]) - _timing_start(timing_map[idx]))


def ensure_timing_map_for_segments(
    segments: Sequence[str],
    timing_map: Sequence[Any] | None,
    *,
    duration_ms: int | None = None,
    default_slot_ms: int = 3000,
) -> list[dict[str, int]]:
    """
    Keep a non-empty timing map aligned with segment count.
    Rebuilds proportional slots when STT/merge left segments without timing.
    """
    texts = [str(s or "").strip() for s in segments if str(s or "").strip()]
    n = len(segments) if segments else len(texts)
    if n <= 0:
        return []

    existing = list(timing_map or [])
    if len(existing) >= n and any(
        _timing_end(existing[i]) > _timing_start(existing[i]) for i in range(n)
    ):
        return [
            {"start": _timing_start(existing[i]), "end": _timing_end(existing[i])}
            for i in range(n)
        ]

    total = int(duration_ms or 0)
    if total <= 0:
        total = max(n * default_slot_ms, default_slot_ms)
    slot = max(500, total // n)
    return [{"start": i * slot, "end": (i + 1) * slot} for i in range(n)]


def merge_stt_segments(
    segments: List[str],
    timing_map: Sequence[Any],
    *,
    min_safe_ms: int = MIN_SAFE_SEGMENT_MS,
    max_gap_ms: int = MAX_GAP_MS,
    max_span_ms: int = MAX_MERGED_SPAN_MS,
) -> tuple[List[str], List[dict]]:
    """
    Склеивает соседние короткие реплики STT в более длинные блоки.
    Возвращает (merged_texts, merged_timing_map dict start/end).
    """
    if not segments:
        return [], []
    if not timing_map:
        return [str(s).strip() for s in segments], []

    n = min(len(segments), len(timing_map))
    out_texts: List[str] = []
    out_timing: List[dict] = []

    i = 0
    while i < n:
        parts: List[str] = []
        start_ms = _timing_start(timing_map[i])
        end_ms = _timing_end(timing_map[i])
        text = str(segments[i] or "").strip()
        if text:
            parts.append(text)
        j = i + 1

        while j < n:
            gap = max(0, _timing_start(timing_map[j]) - end_ms)
            span = end_ms - start_ms
            next_end = _timing_end(timing_map[j])
            combined = next_end - start_ms
            prev = parts[-1] if parts else ""
            ends_sentence = bool(re.search(r"[.!?…]\s*$", prev))
            next_dur = _segment_duration_ms(timing_map, j)

            need_merge = (
                not ends_sentence
                and gap <= max_gap_ms
                and combined <= max_span_ms
                and (
                    span < min_safe_ms
                    or next_dur < SHORT_SEGMENT_MS
                    or gap <= 400
                )
            )
            if not need_merge:
                break

            nxt = str(segments[j] or "").strip()
            if nxt:
                parts.append(nxt)
            end_ms = next_end
            j += 1

        merged = " ".join(parts).strip()
        out_texts.append(merged)
        out_timing.append({"start": start_ms, "end": end_ms})
        i = j

    if len(out_texts) != len(out_timing):
        logger.warning(
            "merge_stt_segments geometry: texts=%d timing=%d",
            len(out_texts),
            len(out_timing),
        )

    logger.info(
        "merge_stt_segments: %d -> %d blocks (min_safe=%dms)",
        n,
        len(out_texts),
        min_safe_ms,
    )
    return out_texts, out_timing


def merge_stt_by_sentences(
    segments: List[str],
    timing_map: Sequence[Any],
    *,
    max_span_ms: int = MAX_MERGED_SPAN_MS,
    max_gap_ms: int = MAX_GAP_MS,
) -> tuple[List[str], List[dict]]:
    """
    Вариант Б: склейка по границам предложений (.!?…).
    Меньше микро-резов — лучше для перевода и интонации.
    """
    if not segments:
        return [], []
    if not timing_map:
        return [str(s).strip() for s in segments], []

    n = min(len(segments), len(timing_map))
    out_texts: List[str] = []
    out_timing: List[dict] = []

    i = 0
    while i < n:
        parts: List[str] = []
        start_ms = _timing_start(timing_map[i])
        end_ms = _timing_end(timing_map[i])
        text = str(segments[i] or "").strip()
        if text:
            parts.append(text)
        j = i + 1

        while j < n:
            prev = parts[-1] if parts else ""
            ends_sentence = bool(re.search(r"[.!?…]\s*$", prev))
            if ends_sentence:
                break

            gap = max(0, _timing_start(timing_map[j]) - end_ms)
            span = end_ms - start_ms
            next_end = _timing_end(timing_map[j])
            combined = next_end - start_ms

            if gap > max_gap_ms or combined > max_span_ms:
                break

            nxt = str(segments[j] or "").strip()
            if nxt:
                parts.append(nxt)
            end_ms = next_end
            j += 1

        merged = " ".join(parts).strip()
        out_texts.append(merged)
        out_timing.append({"start": start_ms, "end": end_ms})
        i = j

    logger.info(
        "merge_stt_by_sentences: %d -> %d sentence blocks",
        n,
        len(out_texts),
    )
    return out_texts, out_timing
