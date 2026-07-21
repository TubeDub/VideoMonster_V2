"""Overflow Manager — MASTER TZ v3.0 P10.

Overflow is a pipeline *state*, not a silent text fix.
Never mutates locked translation text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class OverflowRecord:
    segment_id: str
    index: int
    severity: str  # info | warning | critical
    reason: str
    required_ms: int
    available_ms: int
    overflow_ms: int
    recovery_plan: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_overflow(*, overflow_ms: int, slot_ms: int) -> str:
    if overflow_ms <= 0 or slot_ms <= 0:
        return "info"
    pct = overflow_ms / max(slot_ms, 1)
    if pct > 0.15:
        return "critical"
    if pct > 0.05:
        return "warning"
    return "info"


def build_recovery_plan(*, overflow_ms: int, slot_ms: int) -> list[str]:
    """TZ-ordered recovery plan — never jump to stretch/video before cheaper stages."""
    from engines.dub_engine_v2.overflow_strategy import STRATEGY_ORDER

    plan = [
        "trim_silence",
        "pause_optimization",
        "tempo",
        "stretch",
        "borrow_time",
        "sentence_merge",
        "semantic_rewrite",
        "manual_review",
    ]
    # Keep order identical to STRATEGY_ORDER for contract stability
    ordered = [s for s in STRATEGY_ORDER if s in plan]
    pct = overflow_ms / max(slot_ms, 1) if slot_ms else 0.0
    if pct <= 0.05:
        # Mild: stop before merge/rewrite in the *suggested* plan (still list full chain)
        return ordered
    return ordered


def register_overflow(
    seg: dict[str, Any],
    *,
    index: int = 0,
    overflow_ms: int,
    slot_ms: int,
    reason: str = "audio_longer_than_slot",
    gap_after_ms: int = 0,
    llm_available: bool = False,
) -> OverflowRecord:
    """Stamp overflow state onto segment for Studio (no text mutation)."""
    available = max(0, int(slot_ms))
    required = available + max(0, int(overflow_ms))
    severity = classify_overflow(overflow_ms=overflow_ms, slot_ms=slot_ms)
    plan = build_recovery_plan(overflow_ms=overflow_ms, slot_ms=slot_ms)
    rec = OverflowRecord(
        segment_id=str(seg.get("segment_id") or seg.get("segment_uuid") or index),
        index=int(index),
        severity=severity,
        reason=reason,
        required_ms=required,
        available_ms=available,
        overflow_ms=max(0, int(overflow_ms)),
        recovery_plan=plan,
    )
    seg["overflow"] = True
    seg["slot_overflow"] = True
    seg["overflow_ms"] = rec.overflow_ms
    seg["overflow_pct"] = round(
        (rec.overflow_ms / max(available, 1)) * 100.0, 2
    )
    seg["overflow_manager"] = rec.to_dict()

    # TZ: every overflow must trigger adaptation decision (never silent skip)
    try:
        from engines.dub_engine_v2.overflow_strategy import (
            decide_overflow,
            stamp_decision_on_segment,
        )
        from engines.pipeline_integrity.translation_lock import is_segment_locked

        locked = is_segment_locked(seg)
        already = list((seg.get("text_adaptation_trace") or {}).get("stages") or [])
        decision = decide_overflow(
            index=index,
            overflow_ms=rec.overflow_ms,
            slot_ms=available,
            cause=reason,
            gap_after_ms=int(gap_after_ms or 0),
            llm_available=bool(llm_available),
            text_locked=locked,
            already_applied=already,
        )
        stamp_decision_on_segment(seg, decision)
    except Exception:
        # Planner failed — do not silently claim adaptation_executed=true.
        # Mandatory skip_reason so SUCCESS gate reports OverflowDetected+AdaptationSkipped.
        from engines.dub_engine_v2.adaptation_decision import (
            SKIP_DECISION_ENGINE_RETURNED_SKIP,
            mark_adaptation_skipped,
        )

        mark_adaptation_skipped(
            seg,
            skip_reason=SKIP_DECISION_ENGINE_RETURNED_SKIP,
            index=index,
            overflow_ms=rec.overflow_ms,
            need_adaptation=True,
            decision="overflow_manager_stamp_failed",
        )
    return rec


def collect_overflow_report(segments: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        om = seg.get("overflow_manager")
        if om:
            rows.append(om)
        elif seg.get("overflow") or seg.get("slot_overflow"):
            rows.append(
                {
                    "segment_id": seg.get("segment_id"),
                    "index": i,
                    "overflow_ms": int(seg.get("overflow_ms") or 0),
                    "severity": classify_overflow(
                        overflow_ms=int(seg.get("overflow_ms") or 0),
                        slot_ms=int(seg.get("slot_ms") or 0),
                    ),
                }
            )
    return {
        "count": len(rows),
        "critical": sum(1 for r in rows if r.get("severity") == "critical"),
        "segments": rows,
    }
