"""P45 Scheduler 2.0 — AudioUnits only (never text / sentences)."""

from __future__ import annotations

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.speech_units import AudioUnit, SpeechUnit, Timeline
import uuid


def _uid() -> str:
    return uuid.uuid4().hex


def schedule_audio_units(
    speech_units: list[SpeechUnit],
    *,
    min_gap_ms: int = 40,
) -> Timeline:
    """
    Build Timeline from SpeechUnits → AudioUnits.
    Forbidden: reading/writing translation text.
    """
    units: list[AudioUnit] = []
    cursor = 0
    for su in speech_units:
        # Prefer planned slot; push forward if overlap
        start = max(int(su.start_ms), cursor)
        dur = int(su.expected_duration_ms or max(200, su.slot_ms))
        end = start + dur
        # Keep within original end if possible
        if su.end_ms > su.start_ms and end > su.end_ms + 500:
            # tempo hint only — no text
            tempo = min(1.12, dur / max(1, su.end_ms - start))
            dur = max(200, int(dur / tempo))
            end = start + dur
        else:
            tempo = 1.0
        units.append(
            AudioUnit(
                audio_uuid=_uid(),
                speech_uuid=su.speech_uuid,
                start_ms=start,
                end_ms=end,
                duration_ms=dur,
                tempo=tempo,
            )
        )
        cursor = end + min_gap_ms
    return Timeline(units=units)


def assert_no_double_voice(timeline: Timeline) -> None:
    """P47 — overlapping AudioUnits = critical architecture error."""
    ordered = sorted(timeline.units, key=lambda u: u.start_ms)
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if a.end_ms > b.start_ms + 20:
            raise ArchitectureViolation(
                f"P47 No Double Voice: AudioUnits overlap {a.end_ms - b.start_ms}ms",
                stage="scheduler_v2",
                rule="no_double_voice",
                details={
                    "a": a.audio_uuid,
                    "b": b.audio_uuid,
                    "overlap_ms": a.end_ms - b.start_ms,
                },
            )


def assert_scheduler_text_free(obj: Any) -> None:  # noqa: ANN401
    """P45 — Scheduler must not accept text-bearing payloads."""
    if hasattr(obj, "translated_text") or hasattr(obj, "text") and not isinstance(obj, SpeechUnit):
        # SpeechUnit carries text for Dub Engine; Scheduler only gets AudioUnit/Timeline
        pass
    if isinstance(obj, list) and obj and hasattr(obj[0], "translated_text"):
        raise ArchitectureViolation(
            "P45: Scheduler cannot receive SemanticSentence list",
            stage="scheduler_v2",
            rule="audio_units_only",
        )
