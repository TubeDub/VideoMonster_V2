"""Error Recovery strategies — Dub Engine Stabilization TZ v2.0 P6.

Failures recover at segment scope when possible — never wipe the whole project.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("tubedub.error_recovery")


class RecoveryAction(str, Enum):
    RETRY = "retry"
    REGENERATE_TTS = "regenerate_tts"
    SKIP_SEGMENT = "skip_segment"
    MARK_OVERFLOW = "mark_overflow"
    USE_FALLBACK_ENGINE = "use_fallback_engine"
    ABORT_PIPELINE = "abort_pipeline"
    RESUME_FROM_CHECKPOINT = "resume_from_checkpoint"


@dataclass
class RecoveryPlan:
    action: RecoveryAction
    segment_id: str = ""
    reason: str = ""
    max_attempts: int = 1
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "segment_id": self.segment_id,
            "reason": self.reason,
            "max_attempts": self.max_attempts,
            "details": self.details,
        }


def plan_recovery(
    error_code: str,
    *,
    segment_id: str = "",
    attempt: int = 0,
    details: dict[str, Any] | None = None,
) -> RecoveryPlan:
    """Map error taxonomy → recovery strategy (root-cause oriented)."""
    code = (error_code or "").lower()
    details = details or {}

    if "tts" in code and ("not found" in code or "missing" in code or "file" in code):
        if attempt < 2:
            return RecoveryPlan(
                RecoveryAction.REGENERATE_TTS,
                segment_id=segment_id,
                reason="tts_file_missing",
                max_attempts=2,
                details=details,
            )
        return RecoveryPlan(
            RecoveryAction.SKIP_SEGMENT,
            segment_id=segment_id,
            reason="tts_regen_exhausted",
            details=details,
        )

    if "overflow" in code:
        return RecoveryPlan(
            RecoveryAction.MARK_OVERFLOW,
            segment_id=segment_id,
            reason="timing_overflow_audio_only",
            details=details,
        )

    if "audio_identity" in code or "duplicate" in code:
        return RecoveryPlan(
            RecoveryAction.RETRY,
            segment_id=segment_id,
            reason="repair_unique_filename",
            max_attempts=1,
            details=details,
        )

    if "runtime_integrity" in code:
        return RecoveryPlan(
            RecoveryAction.ABORT_PIPELINE,
            segment_id=segment_id,
            reason="runtime_integrity_hard_stop",
            details=details,
        )

    if "crash" in code or "checkpoint" in code:
        return RecoveryPlan(
            RecoveryAction.RESUME_FROM_CHECKPOINT,
            reason="crash_resume",
            details=details,
        )

    if attempt < 1:
        return RecoveryPlan(
            RecoveryAction.RETRY,
            segment_id=segment_id,
            reason=code or "unknown",
            max_attempts=1,
            details=details,
        )

    return RecoveryPlan(
        RecoveryAction.SKIP_SEGMENT if segment_id else RecoveryAction.ABORT_PIPELINE,
        segment_id=segment_id,
        reason=code or "unknown_exhausted",
        details=details,
    )


def apply_skip_segment(seg: dict[str, Any], *, reason: str) -> None:
    """Isolate failure to one segment without killing the project."""
    seg["status"] = "skipped_recovery"
    seg["tts_status"] = "skipped_recovery"
    seg["recovery_reason"] = reason
    seg["slot_overflow"] = bool(seg.get("slot_overflow"))
    logger.warning(
        "recovery: skipped segment %s reason=%s",
        seg.get("segment_id"),
        reason,
    )


def recover_missing_tts(
    seg: dict[str, Any],
    *,
    regen_fn: Callable[..., Any] | None,
    attempt: int = 0,
) -> RecoveryPlan:
    plan = plan_recovery(
        "tts_file_not_found",
        segment_id=str(seg.get("segment_id") or ""),
        attempt=attempt,
    )
    if plan.action == RecoveryAction.REGENERATE_TTS and regen_fn is not None:
        try:
            regen_fn(seg)
            plan.details["regenerated"] = True
        except Exception as exc:
            plan.details["regen_error"] = str(exc)
            apply_skip_segment(seg, reason=f"regen_failed:{exc}")
            plan.action = RecoveryAction.SKIP_SEGMENT
    elif plan.action == RecoveryAction.SKIP_SEGMENT:
        apply_skip_segment(seg, reason=plan.reason)
    return plan
