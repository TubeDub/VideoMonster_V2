"""
Проверка наложений соседних сегментов и выбор щадящей стратегии исправления.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from pydub import AudioSegment

logger = logging.getLogger("tubedub.overlap_quality")

OVERLAP_TOLERANCE_MS = 40
_MAX_BORROW_MS = 2500


def _parse_timing(item: Any) -> tuple[int, int]:
    if isinstance(item, dict):
        return int(item["start"]), int(item["end"])
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[0]), int(item[1])
    return 0, 0


def speech_window_ms(
    start_ms: int,
    slot_end_ms: int,
    next_start_ms: int | None,
) -> int:
    """Макс. длина аудио до начала следующей реплики (с учётом заимствования паузы)."""
    if next_start_ms is not None:
        gap = max(0, next_start_ms - slot_end_ms)
        borrow = min(gap, _MAX_BORROW_MS)
        return max(0, next_start_ms - start_ms)
    return max(0, slot_end_ms - start_ms) + _MAX_BORROW_MS


def analyze_placed_segments(
    segments_data: list[dict],
    timing_map: list[Any],
) -> list[dict]:
    """Сравнивает длительность TTS с допустимым окном до следующей реплики."""
    placed: list[dict] = []
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        file_name = seg.get("file")
        if not file_name:
            continue

        custom = seg.get("tts_timing")
        if custom and len(custom) >= 2:
            start_ms, end_ms = int(custom[0]), int(custom[1])
        elif idx < len(timing_map):
            start_ms, end_ms = _parse_timing(timing_map[idx])
        else:
            continue

        path = Path(file_name)
        if not path.is_absolute():
            from engines.tts import OUTPUT_DIR

            path = OUTPUT_DIR / path.name
        if not path.exists():
            continue

        try:
            tts_ms = len(AudioSegment.from_file(str(path)))
        except Exception:
            continue

        placed.append(
            {
                "idx": idx,
                "start_ms": start_ms,
                "slot_end_ms": end_ms,
                "tts_ms": tts_ms,
                "text": str(seg.get("text") or ""),
            }
        )

    placed.sort(key=lambda x: x["start_ms"])
    issues: list[dict] = []

    for i, row in enumerate(placed):
        next_start = placed[i + 1]["start_ms"] if i + 1 < len(placed) else None
        window = speech_window_ms(row["start_ms"], row["slot_end_ms"], next_start)
        overflow = max(0, row["tts_ms"] - window)
        ratio = row["tts_ms"] / max(window, 1)

        strategy = "none"
        if overflow > OVERLAP_TOLERANCE_MS:
            # TZ №2: сначала адаптация перевода, потом merge; atempo — только в timing_fit + allow_atempo
            strategy = "adapt_translation"
            if _can_merge_thought(
                row["text"], placed[i + 1]["text"] if i + 1 < len(placed) else ""
            ):
                strategy = "adapt_translation_then_merge"

        issues.append(
            {
                **row,
                "window_ms": window,
                "overflow_ms": overflow,
                "overflow_ratio": round(ratio, 3),
                "next_start_ms": next_start,
                "recommended_strategy": strategy,
            }
        )

    return issues


def _can_merge_thought(text_a: str, text_b: str) -> bool:
    a = str(text_a or "").strip()
    b = str(text_b or "").strip()
    if not a or not b:
        return False
    if re.search(r"[.!?…]\s*$", a):
        return False
    return len(a.split()) + len(b.split()) <= 28


def detect_fitted_overlaps(
    placements: list[dict],
) -> list[dict]:
    """
    placements: [{idx, place_start, fitted_ms, strategy, ...}]
    """
    overlaps: list[dict] = []
    ordered = sorted(placements, key=lambda x: x["place_start"])
    for i in range(len(ordered) - 1):
        cur = ordered[i]
        nxt = ordered[i + 1]
        end_cur = cur["place_start"] + cur["fitted_ms"]
        start_nxt = nxt["place_start"]
        overflow = end_cur - start_nxt
        if overflow > OVERLAP_TOLERANCE_MS:
            overlaps.append(
                {
                    "idx": cur["idx"],
                    "next_idx": nxt["idx"],
                    "overflow_ms": int(overflow),
                    "strategy_used": cur.get("strategy", "unknown"),
                }
            )
    return overlaps


def build_quality_report(
    pre_issues: list[dict],
    fitted_overlaps: list[dict],
    fitted_placements: list[dict],
) -> dict[str, Any]:
    unresolved = [o for o in fitted_overlaps if o["overflow_ms"] > OVERLAP_TOLERANCE_MS]
    return {
        "ok": len(unresolved) == 0,
        "pre_analysis_count": len([x for x in pre_issues if x["overflow_ms"] > OVERLAP_TOLERANCE_MS]),
        "fitted_overlap_count": len(unresolved),
        "unresolved_overlaps": unresolved,
        "pre_issues": pre_issues,
        "fitted_placements": fitted_placements,
    }
