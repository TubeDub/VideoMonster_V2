"""Underflow Manager — MASTER TZ v3.0 P11.

Underflow is handled with natural pause / prosody / padding / breath / silence.
Never expands locked translation text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class UnderflowRecord:
    segment_id: str
    index: int
    severity: str
    reason: str
    shortfall_ms: int
    slot_ms: int
    audio_ms: int
    recovery_plan: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_underflow(*, shortfall_ms: int, slot_ms: int) -> str:
    if shortfall_ms <= 0 or slot_ms <= 0:
        return "info"
    pct = shortfall_ms / max(slot_ms, 1)
    if pct > 0.25:
        return "critical"
    if pct > 0.10:
        return "warning"
    return "info"


def build_recovery_plan(*, shortfall_ms: int) -> list[str]:
    plan = ["natural_pause", "prosody_hold"]
    if shortfall_ms > 200:
        plan.append("padding_silence")
    if shortfall_ms > 500:
        plan.append("breath_insert")
    plan.append("tail_silence")
    # Explicitly forbidden by TZ:
    # plan must never include "text_expansion"
    return plan


def register_underflow(
    seg: dict[str, Any],
    *,
    index: int = 0,
    shortfall_ms: int,
    slot_ms: int,
    audio_ms: int,
    reason: str = "audio_shorter_than_slot",
) -> UnderflowRecord:
    """Stamp underflow state — audio padding only, never text expand."""
    rec = UnderflowRecord(
        segment_id=str(seg.get("segment_id") or seg.get("segment_uuid") or index),
        index=int(index),
        severity=classify_underflow(shortfall_ms=shortfall_ms, slot_ms=slot_ms),
        reason=reason,
        shortfall_ms=max(0, int(shortfall_ms)),
        slot_ms=max(0, int(slot_ms)),
        audio_ms=max(0, int(audio_ms)),
        recovery_plan=build_recovery_plan(shortfall_ms=shortfall_ms),
    )
    seg["underflow"] = True
    seg["underflow_ms"] = rec.shortfall_ms
    seg["underflow_manager"] = rec.to_dict()
    # Clear expand-text flags post-LOCK path
    if seg.get("translation_locked") or seg.get("locked_text"):
        seg["expand_required"] = False
        seg["requires_llm_adaptation"] = False
    return rec


def collect_underflow_report(segments: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        seg.get("underflow_manager")
        for seg in segments
        if isinstance(seg, dict) and seg.get("underflow_manager")
    ]
    return {
        "count": len(rows),
        "critical": sum(1 for r in rows if (r or {}).get("severity") == "critical"),
        "segments": rows,
    }
