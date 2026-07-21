"""Translation Lock + Single Owner + Immutable Segment — Freeze TZ P0.

Text First → Translation Validation → TRANSLATION LOCK → Audio First.

After LOCK the following text fields are immutable. Any mutation raises
TranslationLockError (no silent fix).

Timing / audio fields remain mutable and are owned by Scheduler / TTS / Merge.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from engines.pipeline_integrity.contract_versions import stamp_contract_versions
from engines.pipeline_integrity.exceptions import TranslationLockError
from engines.pipeline_integrity.pipeline_state import (
    PipelineState,
    advance_pipeline_state,
    get_pipeline_state,
)

# ---------------------------------------------------------------------------
# Locked text fields (TZ P0 + practical aliases used in this repo)
# ---------------------------------------------------------------------------

LOCKED_TEXT_FIELDS: frozenset[str] = frozenset(
    {
        # TZ explicit list
        "translated_text",
        "semantic_text",
        "grammar_text",
        "corrected_text",
        "rewritten_text",
        # Repo aliases that carry the same semantic payload
        "translation_text",
        "timing_text",
        "plain_text",
        "final_text",
        "text_for_tts",
        "voice_input",
        "text",
        "locked_text",
        "glossary",
        "context",
        "speaker_text",
        "entities",
    }
)

# After LOCK only these (and known audio/timing extensions) may change.
IMMUTABLE_SEGMENT_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        # TZ explicit timing/audio knobs
        "start_time",
        "end_time",
        "playback_rate",
        "silence_trim",
        "stretch_factor",
        # Existing pipeline timing / audio fields
        "start_ms",
        "end_ms",
        "place_start",
        "place_delay_ms",
        "lead_in_ms",
        "playback_duration",
        "tts_ms",
        "actual_duration_ms",
        "file",
        "fitted_file",
        "fitted_ms",
        "tts_file_path",
        "tts_text",
        "tts_status",
        "status",
        "allow_atempo",
        "slot_overflow",
        "overflow_pct",
        "overflow_ms",
        "overflow",
        "container_status",
        "timing_meta",
        "video_stretch_ratio",
        "video_adapt_mode",
        "block_merged_with_next",
        "merge_adjusted_start",
        "merge_adjusted_slot_ms",
        "slot_fit_attempts",
        "conflict_strategy",
        "conflict_status",
        "merged_into",
        "merged_into_id",
        # Lock metadata itself (idempotent re-stamp)
        "translation_locked",
        "translation_lock_snapshot",
        "pipeline_state",
    }
)

# ---------------------------------------------------------------------------
# Single Owner registry (documented + enforceable)
# ---------------------------------------------------------------------------

FIELD_OWNERS: dict[str, str] = {
    # Source transcript
    "text_source": "Whisper",
    "source_text": "Whisper",
    "original_text": "Whisper",
    "source_word_map": "Whisper",
    # Translation payload
    "translated_text": "Translation Engine",
    "translation_text": "Translation Engine",
    "semantic_text": "Translation Engine",
    "grammar_text": "Translation Engine",
    "corrected_text": "Translation Engine",
    "rewritten_text": "Translation Engine",
    "timing_text": "Translation Engine",
    "plain_text": "Translation Engine",
    "final_text": "Translation Engine",
    "text_for_tts": "Translation Engine",
    "voice_input": "Translation Engine",
    "text": "Translation Engine",  # after STT→translate handoff, target text
    "locked_text": "Translation Engine",
    "glossary": "Translation Engine",
    "context": "Translation Engine",
    "speaker_text": "Translation Engine",
    "entities": "Translation Engine",
    # Timing
    "start_time": "Scheduler",
    "end_time": "Scheduler",
    "start_ms": "Scheduler",
    "end_ms": "Scheduler",
    "place_start": "Scheduler",
    "place_delay_ms": "Scheduler",
    "lead_in_ms": "Scheduler",
    "playback_rate": "Scheduler",
    "silence_trim": "Scheduler",
    "stretch_factor": "Scheduler",
    "timing_meta": "Scheduler",
    # Audio file
    "file": "TTS Engine",
    "tts_file_path": "TTS Engine",
    "tts_text": "TTS Engine",
    "playback_duration": "TTS Engine",
    "tts_ms": "TTS Engine",
    # Final mix
    "merged_track": "Merge Engine",
    "final_audio_path": "Merge Engine",
}

OWNER_FIELD_GROUPS: dict[str, frozenset[str]] = {
    "Whisper": frozenset(
        {"text_source", "source_text", "original_text", "source_word_map"}
    ),
    "Translation Engine": frozenset(LOCKED_TEXT_FIELDS),
    "Scheduler": frozenset(
        {
            "start_time",
            "end_time",
            "start_ms",
            "end_ms",
            "place_start",
            "place_delay_ms",
            "lead_in_ms",
            "playback_rate",
            "silence_trim",
            "stretch_factor",
            "timing_meta",
        }
    ),
    "TTS Engine": frozenset(
        {"file", "tts_file_path", "tts_text", "playback_duration", "tts_ms"}
    ),
    "Merge Engine": frozenset({"merged_track", "final_audio_path"}),
}


def is_segment_locked(seg: dict[str, Any] | None) -> bool:
    if not seg:
        return False
    return bool(seg.get("translation_locked"))


def is_project_locked(info: dict[str, Any] | None) -> bool:
    if not info:
        return False
    if bool(info.get("translation_locked")):
        return True
    state = get_pipeline_state(info)
    return state in {
        PipelineState.LOCKED,
        PipelineState.PLANNED,
        PipelineState.SPEECH_READY,
        PipelineState.SCHEDULED,
        PipelineState.MERGED,
        PipelineState.HANDOFF,
        PipelineState.EXPORTED,
        # Legacy aliases (same members as Spec names)
        PipelineState.OPTIMIZED,
        PipelineState.TTS_READY,
    }


def _text_snapshot(seg: dict[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(seg.get(k)) for k in sorted(LOCKED_TEXT_FIELDS) if k in seg}


def assert_text_field_writable(
    seg: dict[str, Any] | None,
    field: str,
    *,
    mutator: str = "",
) -> None:
    """Raise if ``field`` is a locked text field on a locked segment."""
    if not is_segment_locked(seg):
        return
    if field not in LOCKED_TEXT_FIELDS:
        return
    sid = str((seg or {}).get("segment_id") or "")
    raise TranslationLockError(
        f"TRANSLATION_LOCK: cannot mutate {field!r} on locked segment {sid or '?'}"
        + (f" (mutator={mutator})" if mutator else ""),
        segment_id=sid,
        field=field,
        mutator=mutator,
    )


def assert_segments_text_immutable(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    mutator: str = "",
    stage: str = "",
) -> list[dict[str, Any]]:
    """
    Diff locked text fields for any segment that was (or is) locked.
    Returns structured violations; raises TranslationLockError if any found.
    """
    violations: list[dict[str, Any]] = []
    by_id_before = {
        str(s.get("segment_id") or i): s for i, s in enumerate(before)
    }
    for i, a in enumerate(after):
        sid = str(a.get("segment_id") or i)
        b = by_id_before.get(sid)
        if b is None and i < len(before):
            b = before[i]
        if b is None:
            continue
        locked = is_segment_locked(b) or is_segment_locked(a)
        if not locked:
            continue
        for field in sorted(LOCKED_TEXT_FIELDS):
            if field not in b and field not in a:
                continue
            old_val = b.get(field)
            new_val = a.get(field)
            if old_val == new_val:
                continue
            violations.append(
                {
                    "segment_id": sid,
                    "field": field,
                    "old_value": old_val,
                    "new_value": new_val,
                    "stage": stage,
                    "mutator": mutator,
                    "message": (
                        f"TRANSLATION_LOCK: segment {sid} text field {field!r} "
                        f"changed after lock"
                    ),
                }
            )
    if violations:
        first = violations[0]
        raise TranslationLockError(
            first["message"],
            segment_id=str(first.get("segment_id") or ""),
            field=str(first.get("field") or ""),
            old_value=first.get("old_value"),
            new_value=first.get("new_value"),
            mutator=mutator or stage,
            details={"violations": violations, "stage": stage},
        )
    return violations


def lock_segments(
    segments_data: list[dict[str, Any]],
    *,
    info: dict[str, Any] | None = None,
    advance_state: bool = True,
) -> dict[str, Any]:
    """
    Apply TRANSLATION LOCK after Validation when final text is written.

    - Marks each segment ``translation_locked=True``
    - Stores a text snapshot for diagnostics
    - Stamps contract versions on ``info``
    - Advances pipeline state VALIDATED → LOCKED (or TRANSLATED → VALIDATED → LOCKED)
    """
    rows = segments_data or []
    for seg in rows:
        if not isinstance(seg, dict):
            continue
        seg["translation_locked"] = True
        # Final v3.0: locked plain UK text (no SSML)
        plain = str(
            seg.get("plain_text")
            or seg.get("grammar_text")
            or seg.get("translated_text")
            or seg.get("translation_text")
            or seg.get("text")
            or ""
        ).strip()
        seg["locked_text"] = re.sub(r"<[^>]+>", "", plain).strip()
        seg["translation_lock_snapshot"] = _text_snapshot(seg)

    meta: dict[str, Any] = {
        "locked_segments": sum(
            1 for s in rows if isinstance(s, dict) and s.get("translation_locked")
        ),
        "locked_text_fields": sorted(LOCKED_TEXT_FIELDS),
    }

    if info is not None:
        info["translation_locked"] = True
        info["segments_data"] = rows
        versions = stamp_contract_versions(info)
        meta.update(versions)

        if advance_state:
            current = get_pipeline_state(info)
            post_lock = {
                PipelineState.LOCKED,
                PipelineState.PLANNED,
                PipelineState.SPEECH_READY,
                PipelineState.SCHEDULED,
                PipelineState.MERGED,
                PipelineState.HANDOFF,
                PipelineState.EXPORTED,
                PipelineState.TTS_READY,
                PipelineState.OPTIMIZED,
            }
            if current in post_lock:
                pass
            elif current == PipelineState.TRANSLATED:
                advance_pipeline_state(info, PipelineState.VALIDATED)
                advance_pipeline_state(info, PipelineState.LOCKED)
            elif current == PipelineState.VALIDATED:
                advance_pipeline_state(info, PipelineState.LOCKED)
            else:
                raise TranslationLockError(
                    f"cannot lock from pipeline state {current.value}; "
                    f"expected VALIDATED or TRANSLATED",
                    details={"pipeline_state": current.value},
                )
        meta["pipeline_state"] = get_pipeline_state(info).value
        info["translation_lock"] = meta

    return meta


def owner_of(field: str) -> str | None:
    return FIELD_OWNERS.get(field)


def assert_owner_may_write(field: str, owner: str) -> None:
    """Enforce Single Owner: only the declared owner may write ``field``."""
    declared = FIELD_OWNERS.get(field)
    if declared is None:
        return
    if declared != owner:
        raise TranslationLockError(
            f"Single Owner violation: field {field!r} owned by {declared!r}, "
            f"write attempted by {owner!r}",
            field=field,
            mutator=owner,
            details={"owner": declared, "attempted_by": owner},
        )
