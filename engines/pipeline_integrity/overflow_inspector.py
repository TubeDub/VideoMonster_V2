"""PSA6 — Overflow Inspector + LOCK ordering + residual overflow honesty.

translation_locked must not be a dead-end under huge residual overflow:
open Meaning Fit / manual_review call-points (ordering only — no DSAL/MF algo).

Residual overflow > CRITICAL_OVERFLOW_MS ⇒ no final SUCCESS without
SYNC_FAIL / needs_manual_review.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.pipeline_integrity.overflow_inspector")

CRITICAL_OVERFLOW_MS = 350

# Status tokens (honesty)
SYNC_OVERFLOW_REMAINING = "SYNC_OVERFLOW_REMAINING"
SYNC_FAIL = "SYNC_FAIL"


def _residual_overflow_ms(seg: dict[str, Any]) -> int:
    slot = int(seg.get("slot_ms") or 0)
    tts_ms = int(
        seg.get("playback_duration")
        or seg.get("tts_ms")
        or seg.get("actual_duration_ms")
        or 0
    )
    if slot > 0 and tts_ms > 0:
        return max(0, tts_ms - slot)
    return max(0, int(seg.get("predicted_overflow_ms") or 0))


def inspect_overflow(
    segments_data: list[dict[str, Any]],
    *,
    task_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from engines.pipeline_integrity.v2_gates import overflow_inspector_enabled

    if not overflow_inspector_enabled():
        return {"enabled": False, "critical": 0, "success_allowed": True}

    critical: list[dict[str, Any]] = []
    truncated: list[dict[str, Any]] = []

    for idx, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        overflow = _residual_overflow_ms(seg)
        voice_trunc = bool(
            seg.get("voice_truncated")
            or (seg.get("speech_end") or {}).get("voice_truncated")
        )
        if overflow > CRITICAL_OVERFLOW_MS:
            # PSA6 honesty: cannot remain SUCCESS
            seg["sync_status"] = SYNC_FAIL
            seg["sync_detail"] = SYNC_OVERFLOW_REMAINING
            seg["manual_review"] = True
            seg["needs_manual_review"] = True
            seg["success"] = False
            st = str(seg.get("status") or "").upper()
            if st in ("SUCCESS", "OK", "DONE", ""):
                seg["status"] = SYNC_FAIL
            critical.append(
                {
                    "index": idx,
                    "segment_id": seg.get("segment_id"),
                    "overflow_ms": overflow,
                }
            )
        if voice_trunc:
            seg["success"] = False
            seg["needs_manual_review"] = True
            seg["status"] = "TTS_TRUNCATED"
            seg["sync_status"] = SYNC_FAIL
            truncated.append(
                {"index": idx, "segment_id": seg.get("segment_id")}
            )

    report = {
        "enabled": True,
        "critical": len(critical),
        "truncated": len(truncated),
        "critical_rows": critical[:50],
        "truncated_rows": truncated[:50],
        "success_allowed": len(critical) == 0 and len(truncated) == 0,
        "threshold_ms": CRITICAL_OVERFLOW_MS,
    }
    if task_info is not None:
        task_info["overflow_inspector"] = report
        if critical or truncated:
            task_info["sync_status"] = SYNC_FAIL
            task_info["pipeline_success_allowed"] = False
        else:
            task_info["pipeline_success_allowed"] = True
    return report


def should_lock_translations(
    segments_data: list[dict[str, Any]],
    *,
    slot_budget_ok: bool = True,
) -> tuple[bool, str]:
    """
    Lock only after successful adaptation + slot budget + no critical overflow.
    """
    from engines.pipeline_integrity.v2_gates import overflow_inspector_enabled

    if not overflow_inspector_enabled():
        return True, "overflow_inspector_disabled"

    if not slot_budget_ok:
        return False, "slot_budget_not_ok"

    for seg in segments_data or []:
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        if seg.get("slot_budget_blocked"):
            return False, "slot_budget_blocked"
        overflow = _residual_overflow_ms(seg)
        if overflow > CRITICAL_OVERFLOW_MS:
            return False, "critical_overflow"
        if seg.get("voice_truncated"):
            return False, "voice_truncated"
        if seg.get("needs_manual_review") and str(seg.get("sync_status") or "") in (
            SYNC_OVERFLOW_REMAINING,
            SYNC_FAIL,
        ):
            return False, "sync_overflow_remaining"
    return True, "fit_ok"


def unlock_for_overflow_remediation(
    task_info: dict[str, Any],
    segments_data: list[dict[str, Any]],
    *,
    reason: str = "critical_overflow",
) -> dict[str, Any]:
    """PSA6: break translation_locked dead-end under residual overflow.

    Ordering only — does not run Meaning Fit / DSAL algorithms.
    Opens call-points: meaning_fit_call_point + manual_review_call_point.
    """
    unlocked_rows = 0
    for seg in segments_data or []:
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        overflow = _residual_overflow_ms(seg)
        if overflow <= CRITICAL_OVERFLOW_MS and not seg.get("voice_truncated"):
            continue
        if seg.get("translation_locked"):
            seg["translation_locked"] = False
            unlocked_rows += 1
        seg["needs_manual_review"] = True
        seg["manual_review"] = True
        seg["success"] = False
        seg["sync_status"] = SYNC_FAIL
        seg["sync_detail"] = SYNC_OVERFLOW_REMAINING
        if str(seg.get("status") or "").upper() in ("SUCCESS", "OK", "DONE", ""):
            seg["status"] = SYNC_FAIL
        # Call-points (orchestration flags only)
        seg["meaning_fit_call_point"] = True
        seg["manual_review_call_point"] = True
        seg["lock_ordering_reason"] = reason

    was_locked = bool(task_info.get("translation_locked"))
    if was_locked or unlocked_rows:
        task_info["translation_locked"] = False
        task_info["translation_lock_invalidated"] = True
        task_info["translation_lock_invalid_reason"] = reason

    task_info["meaning_fit_call_point"] = True
    task_info["manual_review_call_point"] = True
    task_info["lock_ordering"] = {
        "translation_locked": False,
        "reason": reason,
        "unlocked_segments": unlocked_rows,
        "next_call_points": ["meaning_fit", "manual_review"],
        "note": "PSA6 ordering only — Meaning Fit / DSAL algorithms not invoked here",
    }
    task_info["translation_lock_deferred"] = reason
    task_info["pipeline_success_allowed"] = False
    task_info["sync_status"] = SYNC_FAIL
    task_info["segments_data"] = segments_data

    logger.info(
        "[PSA6] unlock_for_overflow_remediation reason=%s unlocked=%d "
        "meaning_fit_call_point=1 manual_review_call_point=1",
        reason,
        unlocked_rows,
    )
    return dict(task_info.get("lock_ordering") or {})


def assert_no_success_with_residual_overflow(
    segments_data: list[dict[str, Any]],
    *,
    task_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PSA6: residual overflow > threshold ⇒ forbid final SUCCESS honesty."""
    from engines.pipeline_integrity.v2_gates import overflow_inspector_enabled

    if not overflow_inspector_enabled():
        return {"enabled": False, "ok": True, "demoted": 0}

    demoted = 0
    critical = 0
    for seg in segments_data or []:
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        overflow = _residual_overflow_ms(seg)
        if overflow <= CRITICAL_OVERFLOW_MS:
            continue
        critical += 1
        honest = bool(seg.get("needs_manual_review")) and str(
            seg.get("sync_status") or ""
        ) in (SYNC_FAIL, SYNC_OVERFLOW_REMAINING)
        st = str(seg.get("status") or "").upper()
        if st == "SUCCESS" or seg.get("success") is True or not honest:
            seg["success"] = False
            seg["status"] = SYNC_FAIL
            seg["sync_status"] = SYNC_FAIL
            seg["sync_detail"] = SYNC_OVERFLOW_REMAINING
            seg["needs_manual_review"] = True
            demoted += 1

    ok = critical == 0
    report = {
        "enabled": True,
        "ok": ok,
        "critical": critical,
        "demoted": demoted,
        "success_allowed": ok,
    }
    if task_info is not None:
        task_info["overflow_success_honesty"] = report
        if not ok:
            task_info["pipeline_success_allowed"] = False
            task_info["sync_status"] = SYNC_FAIL
            # Project-level status must not claim SUCCESS
            if str(task_info.get("status") or "").upper() == "SUCCESS":
                task_info["status"] = SYNC_FAIL
    return report


def apply_late_translation_lock(
    task_info: dict[str, Any],
    segments_data: list[dict[str, Any]],
    *,
    slot_budget_ok: bool = True,
) -> dict[str, Any]:
    """Lock only when policy allows; otherwise unlock dead-end + open call-points."""
    # Honesty first — stamp SYNC_FAIL / manual_review on residual overflow
    inspect_overflow(segments_data, task_info=task_info)
    honesty = assert_no_success_with_residual_overflow(
        segments_data, task_info=task_info
    )

    ok, reason = should_lock_translations(
        segments_data, slot_budget_ok=slot_budget_ok
    )
    result: dict[str, Any] = {
        "locked": False,
        "reason": reason,
        "honesty": honesty,
        "unlocked_for_remediation": False,
    }

    if not ok:
        logger.info("[OverflowInspector] defer lock: %s", reason)
        # PSA6: translation_locked=true with huge overflow is a dead-end —
        # unlock and expose Meaning Fit / manual review call-points.
        already_locked = bool(task_info.get("translation_locked")) or any(
            isinstance(s, dict) and s.get("translation_locked")
            for s in (segments_data or [])
        )
        if already_locked or reason in (
            "critical_overflow",
            "sync_overflow_remaining",
            "voice_truncated",
            "slot_budget_not_ok",
            "slot_budget_blocked",
        ):
            ordering = unlock_for_overflow_remediation(
                task_info, segments_data, reason=reason
            )
            result["unlocked_for_remediation"] = True
            result["lock_ordering"] = ordering
        else:
            task_info["translation_lock_deferred"] = reason
            for seg in segments_data:
                if isinstance(seg, dict) and reason in (
                    "critical_overflow",
                    "sync_overflow_remaining",
                    "voice_truncated",
                ):
                    seg["needs_manual_review"] = True
        result["locked"] = False
        return result

    from engines.translation_validation import apply_translation_lock_after_validation

    task_info["segments_data"] = segments_data
    apply_translation_lock_after_validation(task_info)
    result["locked"] = bool(task_info.get("translation_locked"))
    task_info.pop("translation_lock_invalidated", None)
    logger.info("[OverflowInspector] late lock applied reason=%s", reason)
    return result


def apply_psa6_lock_ordering(
    task_info: dict[str, Any],
    segments_data: list[dict[str, Any]],
    *,
    slot_budget_ok: bool = True,
) -> dict[str, Any]:
    """Public PSA6 entry: inspect → honesty → late lock / unlock remediation."""
    ov = inspect_overflow(segments_data, task_info=task_info)
    lock_res = apply_late_translation_lock(
        task_info,
        segments_data,
        slot_budget_ok=slot_budget_ok and bool(ov.get("success_allowed", True)),
    )
    honesty = assert_no_success_with_residual_overflow(
        segments_data, task_info=task_info
    )
    return {
        "overflow": ov,
        "late_lock": lock_res,
        "honesty": honesty,
        "translation_locked": bool(task_info.get("translation_locked")),
        "meaning_fit_call_point": bool(task_info.get("meaning_fit_call_point")),
        "manual_review_call_point": bool(task_info.get("manual_review_call_point")),
        "pipeline_success_allowed": bool(
            task_info.get("pipeline_success_allowed", ov.get("success_allowed"))
        ),
    }
