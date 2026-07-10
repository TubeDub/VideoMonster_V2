"""Timing score from timing_report data or duration prediction."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_timing(
    text: str,
    seg: dict,
    *,
    tgt_lang: str = "ru",
    timing_entry: dict | None = None,
) -> CheckResult:
    entry = timing_entry or {}
    slot_fit = float(entry.get("slot_fit_score") or 0.0)
    if slot_fit > 0:
        score = slot_fit
    else:
        start = seg.get("start")
        end = seg.get("end")
        slot_ms = max(1, int(end) - int(start)) if start is not None and end is not None else 1
        predicted = int(entry.get("predicted_ms") or predict_duration_ms(text, tgt_lang))
        ratio = predicted / slot_ms
        if 0.82 <= ratio <= 1.15:
            score = 1.0
        elif ratio > 1.15:
            score = max(0.0, 1.0 - min(1.0, (ratio - 1.15) * 2.0))
        else:
            score = max(0.0, 1.0 - min(0.85, (0.82 - ratio) * 1.8))

    ok = score >= 0.75
    issues: list[str] = []
    if not ok:
        issues.append("timing_mismatch")
    return CheckResult(
        ok=ok,
        score=round(score, 4),
        failure_type=None if ok else "timing",
        issues=issues,
    )
