"""Read/Write Separation — MASTER TZ v3.0 P27.

Each stage declares which fields it may read and write.
Writes outside the declared set raise ArchitectureViolation immediately.
"""

from __future__ import annotations

from typing import Any, FrozenSet

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.pipeline_integrity.stage_contracts import (
    CORE_IDENTITY_FIELDS,
    POST_LOCK_TIMING_AUDIO_FIELDS,
    STAGE_ALLOWED_MUTATIONS,
    allowed_fields_for_stage,
)

# Fields every stage may read (identity + timing context).
STAGE_READ_FIELDS: dict[str, frozenset[str]] = {
    "slot_fit": frozenset(POST_LOCK_TIMING_AUDIO_FIELDS)
    | CORE_IDENTITY_FIELDS
    | frozenset(
        {
            "text",
            "plain_text",
            "translation_text",
            "translated_text",
            "locked_text",
            "emotion",
            "tts_emotion",
            "translation_locked",
        }
    ),
    "audio_timing": frozenset(POST_LOCK_TIMING_AUDIO_FIELDS)
    | CORE_IDENTITY_FIELDS
    | frozenset({"translation_locked", "locked_text"}),
    "scheduler": frozenset(POST_LOCK_TIMING_AUDIO_FIELDS) | CORE_IDENTITY_FIELDS,
    "tts": frozenset(POST_LOCK_TIMING_AUDIO_FIELDS)
    | CORE_IDENTITY_FIELDS
    | frozenset({"text", "plain_text", "translation_text", "tts_text", "emotion"}),
}


def stage_write_fields(stage: str) -> FrozenSet[str]:
    return frozenset(allowed_fields_for_stage(stage))


def stage_read_fields(stage: str) -> FrozenSet[str]:
    if stage in STAGE_READ_FIELDS:
        return STAGE_READ_FIELDS[stage]
    # Default: may read anything, write only whitelist
    return frozenset({"*"})


def assert_write_allowed(stage: str, field: str) -> None:
    allowed = stage_write_fields(stage)
    if field in CORE_IDENTITY_FIELDS and field not in allowed:
        # identity fields are generally immutable
        raise ArchitectureViolation(
            f"P27: stage {stage!r} cannot write identity field {field!r}",
            stage=stage,
            rule="read_write_separation",
            details={"field": field, "allowed": sorted(allowed)[:40]},
        )
    if field not in allowed:
        raise ArchitectureViolation(
            f"P27: stage {stage!r} cannot write field {field!r}",
            stage=stage,
            rule="read_write_separation",
            details={"field": field, "allowed": sorted(allowed)[:40]},
        )


def declare_stage_contract(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "reads": sorted(stage_read_fields(stage))
        if "*" not in stage_read_fields(stage)
        else ["*"],
        "writes": sorted(stage_write_fields(stage)),
    }
