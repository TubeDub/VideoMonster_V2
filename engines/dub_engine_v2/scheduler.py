"""P408 Scheduler 2.0 — sole owner of time for Audio Units."""

from __future__ import annotations

import uuid
from typing import Any

from engines.dub_engine_v2.models import AudioUnitV2, ProjectTimeline, SpeechUnitV2
from engines.dub_engine_v2.timing import TimingAdjustment
from engines.pipeline_integrity.exceptions import ArchitectureViolation


def _uid() -> str:
    return uuid.uuid4().hex


def schedule_project(
    speech_units: list[SpeechUnitV2],
    adjustments: list[TimingAdjustment],
    *,
    min_gap_ms: int = 40,
    lipsync_alignment: dict[str, tuple[dict[str, Any], ...]] | None = None,
) -> ProjectTimeline:
    """
    Build ProjectTimeline from SpeechUnits + timing adjustments.
    Forbidden: accepting/mutating translation text.
    """
    adj_map = {a.speech_uuid: a for a in adjustments}
    units: list[AudioUnitV2] = []
    pauses: list[dict[str, int]] = []
    cursor = 0

    for su in speech_units:
        # Guard: do not schedule from sentence objects
        if hasattr(su, "translated_text") and not isinstance(su, SpeechUnitV2):
            raise ArchitectureViolation(
                "P408: Scheduler accepts SpeechUnitV2 only",
                stage="dub_scheduler",
                rule="audio_units_only",
            )
        adj = adj_map.get(su.speech_uuid)
        dur = int(
            (adj.expected_duration_ms if adj else 0)
            or su.expected_duration
            or su.predicted_duration
            or max(200, su.slot_ms)
        )
        start = max(int(su.start_ms), cursor)
        end = start + dur
        if adj:
            end += int(adj.pause_ms or 0) + int(adj.breath_ms or 0)
            dur = end - start
        align = ()
        if lipsync_alignment and su.speech_uuid in lipsync_alignment:
            align = lipsync_alignment[su.speech_uuid]
        units.append(
            AudioUnitV2(
                audio_uuid=_uid(),
                speech_uuid=su.speech_uuid,
                duration=dur,
                start_ms=start,
                end_ms=end,
                tempo=float(adj.tempo if adj else 1.0),
                stretch=float(adj.stretch if adj else 1.0),
                pause_ms=int(adj.pause_ms if adj else 0),
                breath_ms=int(adj.breath_ms if adj else 0),
                alignment=align,
                audio_state="planned",
            )
        )
        if adj and (adj.pause_ms or adj.breath_ms):
            pauses.append(
                {
                    "after_audio": units[-1].audio_uuid,
                    "pause_ms": int(adj.pause_ms or 0),
                    "breath_ms": int(adj.breath_ms or 0),
                }
            )
        cursor = end + min_gap_ms

    return ProjectTimeline(units=units, pauses=pauses)


def update_audio_time(
    timeline: ProjectTimeline,
    audio_uuid: str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> ProjectTimeline:
    """Sole API to change AudioUnit timing (immutable evolve)."""
    new_units: list[AudioUnitV2] = []
    found = False
    for u in timeline.units:
        if u.audio_uuid != audio_uuid:
            new_units.append(u)
            continue
        found = True
        s = u.start_ms if start_ms is None else int(start_ms)
        e = u.end_ms if end_ms is None else int(end_ms)
        if e < s:
            raise ArchitectureViolation(
                "P408: end_ms < start_ms",
                stage="dub_scheduler",
                rule="scheduler_api",
            )
        new_units.append(u.evolve(start_ms=s, end_ms=e, duration=e - s))
    if not found:
        raise ArchitectureViolation(
            f"P408: unknown audio_uuid {audio_uuid}",
            stage="dub_scheduler",
            rule="scheduler_api",
        )
    timeline.units = new_units
    return timeline
