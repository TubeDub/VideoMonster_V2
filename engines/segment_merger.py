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
# Happy Path (TZ Stage 2): target 5.0s blocks (floor 4.5s), glue across short pauses.
HAPPY_PATH_MIN_SAFE_MS = 5000
HAPPY_PATH_MAX_GAP_MS = 900  # pause < 0.9s
MAX_MERGED_SPAN_MS = 14000
MAX_GAP_MS = 900  # align default with Happy Path pause window
SHORT_SEGMENT_MS = 2500  # pre-TTS / filler shorts (< 2.5–3s)

# CJK / Arabic etc. — sentence ends are not Latin .!?
_CJK_SENTENCE_END = re.compile(r"[。！？!?…．]\s*$")
_CJK_CHARS = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _ends_sentence(text: str) -> bool:
    t = str(text or "").rstrip()
    if not t:
        return False
    if re.search(r"[.!?…]\s*$", t):
        return True
    return bool(_CJK_SENTENCE_END.search(t))


def _looks_cjk(segments: Sequence[str]) -> bool:
    sample = " ".join(str(s or "") for s in list(segments)[:8])
    return len(_CJK_CHARS.findall(sample)) >= 8


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
    fill_to_min: bool = True,
    speaker_ids: Sequence[Any] | None = None,
) -> tuple[List[str], List[dict]]:
    """
    Склеивает соседние короткие реплики STT в более длинные блоки.
    Возвращает (merged_texts, merged_timing_map dict start/end).

    ``fill_to_min`` (Happy Path / TZ Stage 2): keep gluing while span < min_safe_ms
    even after a sentence end, as long as the pause is ≤ max_gap_ms and speakers match.
    """
    if not segments:
        return [], []
    if not timing_map:
        return [str(s).strip() for s in segments], []

    # CJK: shorter blocks + break on pauses — Latin .!? never appear
    cjk = _looks_cjk(segments)
    if cjk:
        min_safe_ms = min(min_safe_ms, 2800)
        max_span_ms = min(max_span_ms, 8000)
        max_gap_ms = min(max_gap_ms, 700)

    n = min(len(segments), len(timing_map))
    out_texts: List[str] = []
    out_timing: List[dict] = []

    def _same_speaker(a: int, b: int) -> bool:
        if not speaker_ids or a >= len(speaker_ids) or b >= len(speaker_ids):
            return True
        sa, sb = speaker_ids[a], speaker_ids[b]
        if sa is None or sb is None or sa == "" or sb == "":
            return True
        return sa == sb

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
            ends_sentence = _ends_sentence(prev)
            next_dur = _segment_duration_ms(timing_map, j)

            if not _same_speaker(j - 1, j):
                break

            # Fill short blocks up to min_safe even across sentence ends (TZ).
            under_min = fill_to_min and span < min_safe_ms
            need_merge = (
                gap <= max_gap_ms
                and combined <= max_span_ms
                and (
                    under_min
                    or (
                        not ends_sentence
                        and (
                            span < min_safe_ms
                            or next_dur < SHORT_SEGMENT_MS
                            or gap <= (250 if cjk else 400)
                        )
                    )
                )
            )
            # Without fill_to_min, never cross a finished sentence.
            if not fill_to_min and ends_sentence:
                need_merge = False
            nxt = str(segments[j] or "").strip()
            # Drama turn markers (妈 / 我怀孕了 / …) must not be glued into prior speech
            if cjk and need_merge:
                try:
                    from engines.mt.zh_asr_correct import is_cjk_turn_break

                    if is_cjk_turn_break(nxt) or is_cjk_turn_break(prev):
                        need_merge = False
                except Exception:
                    pass
            if not need_merge:
                break

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
        "merge_stt_segments: %d -> %d blocks (min_safe=%dms gap=%dms fill=%s cjk=%s)",
        n,
        len(out_texts),
        min_safe_ms,
        max_gap_ms,
        fill_to_min,
        cjk,
    )
    return out_texts, out_timing


def merge_stt_segments_happy_path(
    segments: List[str],
    timing_map: Sequence[Any],
    *,
    min_safe_ms: int = HAPPY_PATH_MIN_SAFE_MS,
    max_gap_ms: int = HAPPY_PATH_MAX_GAP_MS,
    max_span_ms: int = MAX_MERGED_SPAN_MS,
    speaker_ids: Sequence[Any] | None = None,
) -> tuple[List[str], List[dict]]:
    """TZ Stage 2 Happy Path STT glue: ≥5.0s default (UK Simple may use ≥4.0s).

    Stage 29 §D — when callers pass ``min_safe_ms=4000`` (UK Simple ~4/7/12),
    honour a 4.0s floor instead of the legacy 4.5s clamp.
    """
    requested = int(min_safe_ms or HAPPY_PATH_MIN_SAFE_MS)
    floor = 4000 if requested <= 4000 else 4500
    safe = max(floor, requested)
    gap = max(900, int(max_gap_ms or HAPPY_PATH_MAX_GAP_MS))
    # Never allow a looser-than-requested gap below the TZ floor of 900ms.
    gap = max(gap, HAPPY_PATH_MAX_GAP_MS)
    texts, timing = merge_stt_segments(
        segments,
        timing_map,
        min_safe_ms=safe,
        max_gap_ms=gap,
        max_span_ms=max_span_ms,
        fill_to_min=True,
        speaker_ids=speaker_ids,
    )
    logger.info(
        "happy_path_stt_merge: segments_before=%d segments_after=%d "
        "min_safe_ms=%d max_gap_ms=%d",
        len(segments or []),
        len(texts or []),
        safe,
        gap,
    )
    return texts, timing


def merge_stt_by_sentences(
    segments: List[str],
    timing_map: Sequence[Any],
    *,
    max_span_ms: int = MAX_MERGED_SPAN_MS,
    max_gap_ms: int = MAX_GAP_MS,
) -> tuple[List[str], List[dict]]:
    """
    Вариант Б: склейка по границам предложений (.!?… / 。！？).
    Меньше микро-резов — лучше для перевода и интонации.
    """
    if not segments:
        return [], []
    if not timing_map:
        return [str(s).strip() for s in segments], []

    cjk = _looks_cjk(segments)
    if cjk:
        # Without Latin punctuation, pause gaps become sentence boundaries
        max_span_ms = min(max_span_ms, 8000)
        max_gap_ms = min(max_gap_ms, 700)

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
            if _ends_sentence(prev):
                break

            gap = max(0, _timing_start(timing_map[j]) - end_ms)
            span = end_ms - start_ms
            next_end = _timing_end(timing_map[j])
            combined = next_end - start_ms

            if gap > max_gap_ms or combined > max_span_ms:
                break
            # CJK: also break on medium pauses even mid-span
            if cjk and gap >= 450 and span >= 1800:
                break

            nxt = str(segments[j] or "").strip()
            if cjk:
                try:
                    from engines.mt.zh_asr_correct import is_cjk_turn_break

                    if is_cjk_turn_break(nxt) or is_cjk_turn_break(prev):
                        break
                except Exception:
                    pass
            if nxt:
                parts.append(nxt)
            end_ms = next_end
            j += 1

        merged = " ".join(parts).strip()
        out_texts.append(merged)
        out_timing.append({"start": start_ms, "end": end_ms})
        i = j

    logger.info(
        "merge_stt_by_sentences: %d -> %d sentence blocks (cjk=%s)",
        n,
        len(out_texts),
        cjk,
    )
    return out_texts, out_timing


def split_overlong_cjk_segments(
    segments: List[str],
    timing_map: Sequence[Any],
    *,
    video_duration_ms: int | None = None,
    max_chars: int = 36,
    max_span_ms: int = 8000,
) -> tuple[List[str], List[dict]]:
    """Split a single overlong CJK STT blob into phrase-sized dub turns.

    Whisper sometimes returns one sparse island holding an entire drama monologue
    (``_tmp_3333``: ~68s video → 1×8s segment). Merger only merges — this
    splitter recovers multi-turn coverage for translation/TTS.
    """
    texts = [str(s or "").strip() for s in (segments or [])]
    if not texts:
        return [], []

    cjk = _looks_cjk(texts)
    if not cjk:
        # Pass through non-CJK unchanged (keep timing shape)
        out_t: List[dict] = []
        for i, t in enumerate(texts):
            if i < len(timing_map or []):
                out_t.append(
                    {
                        "start": _timing_start(timing_map[i]),
                        "end": _timing_end(timing_map[i]),
                    }
                )
            else:
                out_t.append({"start": i * 3000, "end": (i + 1) * 3000})
        return texts, out_t

    # Trigger: one bloated text unit, or slot << video with dense CJK
    total_chars = sum(len(_CJK_CHARS.findall(t)) for t in texts)
    vid = int(video_duration_ms or 0)
    need_split = False
    if len(texts) == 1 and total_chars >= max_chars:
        need_split = True
    if len(texts) == 1 and vid > 0:
        span = 0
        if timing_map:
            span = max(0, _timing_end(timing_map[0]) - _timing_start(timing_map[0]))
        if total_chars >= 24 and (span <= 0 or span * 2 < vid or span > max_span_ms):
            need_split = True
    if not need_split:
        out_t = []
        for i, _t in enumerate(texts):
            if i < len(timing_map or []):
                out_t.append(
                    {
                        "start": _timing_start(timing_map[i]),
                        "end": _timing_end(timing_map[i]),
                    }
                )
            else:
                out_t.append({"start": i * 3000, "end": (i + 1) * 3000})
        return texts, out_t

    blob = " ".join(texts).strip()
    # Prefer CJK punctuation, then spaces between phrases
    parts = re.split(r"(?<=[。！？!?…．])\s*", blob)
    parts = [p.strip() for p in parts if p and p.strip()]
    if len(parts) <= 1:
        # Space-separated Whisper tokens / phrases
        toks = [t for t in blob.split() if t.strip()]
        parts = []
        buf: list[str] = []
        buf_chars = 0
        for tok in toks:
            n = len(_CJK_CHARS.findall(tok)) or len(tok)
            if buf and buf_chars + n > max_chars:
                parts.append(" ".join(buf))
                buf = [tok]
                buf_chars = n
            else:
                buf.append(tok)
                buf_chars += n
        if buf:
            parts.append(" ".join(buf))
    if len(parts) <= 1:
        # Hard char windows
        chars = list(blob.replace(" ", ""))
        parts = []
        step = max(12, max_chars)
        for i in range(0, len(chars), step):
            chunk = "".join(chars[i : i + step]).strip()
            if chunk:
                parts.append(chunk)

    if len(parts) <= 1:
        out_t = []
        if timing_map:
            out_t.append(
                {
                    "start": _timing_start(timing_map[0]),
                    "end": _timing_end(timing_map[0]),
                }
            )
        else:
            out_t.append({"start": 0, "end": max(3000, vid or 3000)})
        return texts, out_t

    # Allocate timing: stretch across video when slot is sparse
    if timing_map:
        start0 = _timing_start(timing_map[0])
        end0 = _timing_end(timing_map[0])
    else:
        start0, end0 = 0, max(3000, vid or 3000)
    span0 = max(500, end0 - start0)
    if vid > 0 and span0 * 2 < vid:
        # Sparse island — redistribute across most of the media
        start0 = max(0, int(vid * 0.02))
        end0 = max(start0 + 1000, int(vid * 0.98))
        span0 = end0 - start0

    weights = [max(1, len(_CJK_CHARS.findall(p)) or len(p)) for p in parts]
    total_w = sum(weights) or len(parts)
    out_texts: List[str] = []
    out_timing: List[dict] = []
    cursor = start0
    for i, (p, w) in enumerate(zip(parts, weights)):
        if i == len(parts) - 1:
            end = end0
        else:
            end = cursor + max(800, int(span0 * (w / total_w)))
            end = min(end, end0 - 400 * (len(parts) - i - 1))
        out_texts.append(p)
        out_timing.append({"start": int(cursor), "end": int(max(cursor + 500, end))})
        cursor = out_timing[-1]["end"]

    logger.info(
        "split_overlong_cjk_segments: %d -> %d parts (chars=%d video_ms=%s)",
        len(texts),
        len(out_texts),
        total_chars,
        vid or "-",
    )
    return out_texts, out_timing
