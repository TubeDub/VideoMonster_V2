"""Slot fit — reuse timing_agent slot_fit_validator."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
from engines.ai_core.timing_agent.validators.slot_fit_validator import validate_slot_fit


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)
    predicted_ms: int = 0
    slot_ms: int = 0


def _slot_ms(seg: dict) -> int:
    start = seg.get("start")
    end = seg.get("end")
    if start is not None and end is not None:
        return max(1, int(end) - int(start))
    return max(1, int(seg.get("slot_ms") or 0))


def check_slot_fit(
    text: str,
    seg: dict,
    *,
    tgt_lang: str = "ru",
    timing_entry: dict | None = None,
) -> CheckResult:
    slot = int((timing_entry or {}).get("slot_ms") or _slot_ms(seg))
    predicted = int((timing_entry or {}).get("predicted_ms") or predict_duration_ms(text, tgt_lang))
    result = validate_slot_fit(predicted, slot)
    issues: list[str] = []
    if not result.ok:
        issues.append(f"delta_ms={result.delta_ms}")
    return CheckResult(
        ok=result.ok,
        score=result.score,
        failure_type=None if result.ok else "slot_fit",
        issues=issues,
        predicted_ms=predicted,
        slot_ms=slot,
    )
