"""Extract speech rhythm from original audio for dub prosody (no translation changes)."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.professional_dubbing.source_cues")

_SILENCE_THRESH = -38
_MIN_SILENCE = 60


@lru_cache(maxsize=4)
def _load_audio(path: str):
    from pydub import AudioSegment

    return AudioSegment.from_file(path)


def extract_slot_cues(
    audio_path: str | Path | None,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    """
    Analyze original speech in [start_ms, end_ms].
    Returns lead/tail silence, internal gaps, placement hints.
    """
    empty = {
        "lead_silence_ms": 0,
        "tail_silence_ms": 0,
        "speech_ms": 0,
        "slot_ms": max(0, end_ms - start_ms),
        "internal_gaps_ms": [],
        "speech_ratio": 0.0,
        "place_delay_ms": 0,
        "lead_break_ms": 0,
        "suggested_rate_slow": 0,
    }
    if not audio_path or not Path(audio_path).is_file():
        return empty

    slot_ms = max(0, end_ms - start_ms)
    if slot_ms < 80:
        return empty

    try:
        from pydub.silence import detect_nonsilent

        clip = _load_audio(str(audio_path))[max(0, start_ms) : end_ms]
        if len(clip) < 80:
            return empty

        ranges = detect_nonsilent(
            clip, min_silence_len=_MIN_SILENCE, silence_thresh=_SILENCE_THRESH
        )
        if not ranges:
            return empty

        lead = int(ranges[0][0])
        tail = max(0, len(clip) - int(ranges[-1][1]))
        speech = sum(int(e - s) for s, e in ranges)
        gaps: list[int] = []
        for i in range(len(ranges) - 1):
            gap = int(ranges[i + 1][0] - ranges[i][1])
            if gap >= 80:
                gaps.append(gap)

        ratio = speech / max(len(clip), 1)
        # Match original speaker entry: delay dub start slightly when source has lead-in.
        place_delay = min(280, max(0, int(lead * 0.65))) if lead >= 120 else 0
        lead_break = min(520, max(240, int(lead * 0.55))) if lead >= 160 else 0
        if gaps and gaps[0] >= 200 and not lead_break:
            lead_break = min(500, gaps[0])

        slow = 0
        if ratio > 0.82:
            slow = 3
        elif ratio < 0.55:
            slow = -2

        return {
            "lead_silence_ms": lead,
            "tail_silence_ms": tail,
            "speech_ms": speech,
            "slot_ms": slot_ms,
            "internal_gaps_ms": gaps[:8],
            "speech_ratio": round(ratio, 3),
            "place_delay_ms": place_delay,
            "lead_break_ms": lead_break,
            "suggested_rate_slow": slow,
        }
    except Exception as exc:
        logger.debug("source_cues failed: %s", exc)
        return empty


def gap_to_break_ms(gap_ms: int) -> int | None:
    """Map source silence to SSML break; None = use punctuation default only."""
    if gap_ms < 150:
        return None
    if gap_ms >= 520:
        return min(680, gap_ms)
    if gap_ms >= 320:
        return min(480, gap_ms)
    if gap_ms >= 200:
        return min(360, gap_ms)
    return min(280, max(180, gap_ms))
