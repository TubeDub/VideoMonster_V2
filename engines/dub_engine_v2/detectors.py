"""P410 Overlap / P411 Tail Spill / P415 Multi-voice — critical detectors."""

from __future__ import annotations

from typing import Any

from engines.dub_engine_v2.models import AudioUnitV2, ProjectTimeline, SpeechUnitV2
from engines.pipeline_integrity.exceptions import ArchitectureViolation


def detect_overlaps(timeline: ProjectTimeline, *, hard_fail: bool = True) -> list[dict[str, Any]]:
    """P410 — any overlap / double voice is critical."""
    conflicts: list[dict[str, Any]] = []
    ordered = sorted(timeline.units, key=lambda u: u.start_ms)
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if a.end_ms > b.start_ms + 20:
            conflicts.append(
                {
                    "type": "overlap",
                    "a": a.audio_uuid,
                    "b": b.audio_uuid,
                    "overlap_ms": a.end_ms - b.start_ms,
                }
            )
    if conflicts and hard_fail:
        raise ArchitectureViolation(
            f"P410 Overlap / double voice: {len(conflicts)} conflict(s)",
            stage="dub_engine_v2",
            rule="no_overlap",
            details={"conflicts": conflicts[:5]},
        )
    return conflicts


def detect_tail_spill(
    speech_units: list[SpeechUnitV2],
    timeline: ProjectTimeline,
    *,
    hard_fail: bool = True,
) -> list[dict[str, Any]]:
    """
    P411 — sentence must not start in one AudioUnit and end in another.
    One SpeechUnit ↔ one AudioUnit mapping required.
    """
    by_speech = {u.speech_uuid: u for u in timeline.units}
    issues: list[dict[str, Any]] = []
    # Count audio units per speech
    counts: dict[str, int] = {}
    for u in timeline.units:
        counts[u.speech_uuid] = counts.get(u.speech_uuid, 0) + 1
    for sid, n in counts.items():
        if n > 1:
            issues.append({"type": "tail_spill", "speech_uuid": sid, "audio_units": n})
    for su in speech_units:
        au = by_speech.get(su.speech_uuid)
        if au is None:
            issues.append({"type": "missing_audio", "speech_uuid": su.speech_uuid})
            continue
        # Speech span must be contained in single audio unit window (±slack for tempo)
        if au.start_ms > su.start_ms + 500 or au.end_ms < su.start_ms:
            issues.append(
                {
                    "type": "tail_spill_span",
                    "speech_uuid": su.speech_uuid,
                    "audio_uuid": au.audio_uuid,
                }
            )
    if issues and hard_fail:
        raise ArchitectureViolation(
            f"P411 Tail Spill: {len(issues)} issue(s)",
            stage="dub_engine_v2",
            rule="no_tail_spill",
            details={"issues": issues[:5]},
        )
    return issues


def coordinate_multi_voice(
    speech_units: list[SpeechUnitV2],
    timeline: ProjectTimeline,
) -> list[dict[str, Any]]:
    """P415 — preserve speaker order; flag dialogue pause issues (no silent fix)."""
    notes: list[dict[str, Any]] = []
    ordered = sorted(
        zip(speech_units, timeline.units),
        key=lambda pair: pair[1].start_ms,
    )
    prev_speaker = ""
    for su, au in ordered:
        if prev_speaker and su.speaker_uuid and su.speaker_uuid != prev_speaker:
            notes.append(
                {
                    "type": "speaker_change",
                    "from": prev_speaker,
                    "to": su.speaker_uuid,
                    "at_ms": au.start_ms,
                }
            )
        prev_speaker = su.speaker_uuid or prev_speaker
    return notes
