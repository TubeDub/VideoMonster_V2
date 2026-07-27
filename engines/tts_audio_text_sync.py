# -*- coding: utf-8 -*-
"""Keep Review Final / TTS text honest after hard audio trim (trim_overlap).

When speech is sliced to fit a slot without rewriting text, the UI previously
kept the full paragraph while playback stopped mid-sentence. Sync the spoken
prefix into Final/TTS so Translation Review matches what the listener hears.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TEXT_KEYS = (
    "final_text",
    "text",
    "plain_text",
    "tts_text",
    "text_for_tts",
    "final_tts_text",
    "translation_text",
    "translated_text",
)


def estimate_spoken_prefix(text: str, *, tts_ms: int, spoken_ms: int) -> str:
    """Approximate the words that fit into spoken_ms of a tts_ms utterance."""
    from engines.translation_review_diagnostics import overflow_text_split

    t = str(text or "").strip()
    if not t:
        return ""
    tts = max(0, int(tts_ms or 0))
    spoken = max(0, int(spoken_ms or 0))
    if tts <= 0 or spoken <= 0 or spoken >= tts:
        return t
    split = overflow_text_split(t, slot_ms=spoken, tts_ms=tts)
    fits = str(split.get("fits") or "").strip()
    if not fits:
        return t
    # Prefer ending on sentence / clause punctuation when close to the cut.
    if len(fits) < len(t) and fits[-1].isalnum():
        clause = max(fits.rfind(". "), fits.rfind("! "), fits.rfind("? "), fits.rfind(", "))
        if clause > len(fits) * 0.55:
            fits = fits[: clause + 1].strip()
    return fits or t


def _strategy_trimmed(strategy: str) -> bool:
    s = str(strategy or "").lower()
    return "trim_overlap" in s


def apply_audio_trim_text_sync(
    segments_data: list,
    fitted_placements: list[dict[str, Any]],
    *,
    placed_seg_indices: list[int] | None = None,
    audits: list | None = None,
) -> int:
    """Shrink segment/audit text to the spoken prefix when trim_overlap cut audio.

    ``fitted_placements[i]`` maps to ``segments_data[placed_seg_indices[i]]``
    (or ``segments_data[i]`` when indices are omitted).
    """
    if not segments_data or not fitted_placements:
        return 0

    audit_by: dict[int, dict] = {}
    for row in audits or []:
        if isinstance(row, dict) and "index" in row:
            try:
                audit_by[int(row["index"])] = row
            except (TypeError, ValueError):
                pass

    synced = 0
    for place in fitted_placements:
        if not isinstance(place, dict):
            continue
        try:
            pidx = int(place.get("idx", -1))
        except (TypeError, ValueError):
            continue
        if pidx < 0:
            continue
        if placed_seg_indices and pidx < len(placed_seg_indices):
            seg_i = int(placed_seg_indices[pidx])
        else:
            seg_i = pidx
        if seg_i < 0 or seg_i >= len(segments_data):
            continue
        seg = segments_data[seg_i]
        if not isinstance(seg, dict):
            continue

        strategy = str(place.get("strategy") or (seg.get("timing_meta") or {}).get("strategy") or "")
        if not _strategy_trimmed(strategy) and not place.get("speech_trimmed"):
            continue

        tts_ms = int(place.get("tts_ms") or seg.get("tts_ms") or 0)
        speech_ms = int(
            place.get("speech_ms")
            or place.get("fitted_ms")
            or seg.get("fitted_ms")
            or 0
        )
        # Drop trailing pad from fitted length when present.
        pause = int(place.get("pause_added_ms") or 0)
        if pause > 0 and speech_ms > pause:
            speech_ms = speech_ms - pause

        if tts_ms <= 0 or speech_ms <= 0:
            continue
        # Only sync when a meaningful chunk of speech was dropped.
        if speech_ms >= int(tts_ms * 0.92):
            continue

        full = str(
            seg.get("text_before_audio_fit")
            or seg.get("final_text")
            or seg.get("tts_text")
            or seg.get("text")
            or seg.get("plain_text")
            or ""
        ).strip()
        if not full:
            continue

        spoken = estimate_spoken_prefix(full, tts_ms=tts_ms, spoken_ms=speech_ms)
        if not spoken or spoken == full:
            # Still mark truncation so Review does not resurrect the full Final.
            seg["voice_truncated"] = True
            seg["audio_trim_sync"] = "flag_only"
            continue

        if not seg.get("text_before_audio_fit"):
            seg["text_before_audio_fit"] = full
        for key in _TEXT_KEYS:
            seg[key] = spoken
        # Keep approved in sync when present (TPS single text).
        if seg.get("approved_text"):
            seg["approved_text"] = spoken
        seg["voice_truncated"] = True
        seg["audio_trim_sync"] = "spoken_prefix"
        tm = dict(seg.get("timing_meta") or {})
        tm["speech_trimmed"] = True
        tm["spoken_fit_text"] = spoken
        tm["tts_ms_before_trim"] = tts_ms
        tm["speech_ms_after_trim"] = speech_ms
        seg["timing_meta"] = tm

        audit = audit_by.get(seg_i)
        if audit is not None:
            if not audit.get("text_before_audio_fit"):
                audit["text_before_audio_fit"] = full
            audit["final_text"] = spoken
            audit["tts_text"] = spoken
            audit["plain_text"] = spoken
            audit["voice_truncated"] = True

        synced += 1
        logger.info(
            "audio_trim_text_sync: seg=%s tts=%sms speech=%sms chars %s→%s",
            seg_i,
            tts_ms,
            speech_ms,
            len(full),
            len(spoken),
        )

    return synced


def prefer_spoken_over_longer_final(
    *,
    final: str,
    spoken: str,
    seg: dict | None = None,
    audit: dict | None = None,
) -> str:
    """Review helper: after hard trim, never show a longer Final than spoken TTS."""
    f = str(final or "").strip()
    s = str(spoken or "").strip()
    if not s:
        return f
    if not f:
        return s
    truncated = bool(
        (seg or {}).get("voice_truncated")
        or (audit or {}).get("voice_truncated")
        or ((seg or {}).get("timing_meta") or {}).get("speech_trimmed")
        or (audit or {}).get("audio_trim_sync")
    )
    strategy = str(
        ((seg or {}).get("timing_meta") or {}).get("strategy")
        or (audit or {}).get("timing_strategy")
        or ""
    )
    if truncated or _strategy_trimmed(strategy):
        if len(s) + 8 < len(f) and (f.startswith(s) or s in f[: max(len(s) + 40, 1)]):
            return s
        if len(s) < len(f) * 0.92:
            return s
    return f if f else s
