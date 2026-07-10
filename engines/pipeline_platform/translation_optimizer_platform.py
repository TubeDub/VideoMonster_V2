"""
Translation Optimizer — Stage 3 (mandatory TZ).

Does NOT translate. Does NOT change meaning.
Runs ONLY after translation. Rolls back if quality drops.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OptimizerStep:
    action: str
    before: str
    after: str
    accepted: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "before": self.before,
            "after": self.after,
            "accepted": self.accepted,
            "reason": self.reason,
        }


@dataclass
class OptimizerResult:
    original: str
    optimized: str
    changed: bool
    steps: list[OptimizerStep] = field(default_factory=list)
    quality_before: dict[str, Any] = field(default_factory=dict)
    quality_after: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "optimized": self.optimized,
            "changed": self.changed,
            "steps": [s.to_dict() for s in self.steps],
            "quality_before": dict(self.quality_before),
            "quality_after": dict(self.quality_after),
            "warnings": list(self.warnings),
        }


_FILLER_PATTERNS = [
    (r"\b(ну|отже|тобто|звичайно|власне|actually|basically|literally|just|really)\b", "filler"),
    (r"\s{2,}", "spaces"),
]


def _estimate_ms(text: str, lang: str = "") -> int:
    try:
        from engines.semantic_adaptation import estimate_tts_duration_ms

        return int(estimate_tts_duration_ms(text, lang=lang or "uk"))
    except Exception:
        return max(80, len(text.split()) * 180)


def _quality_check(original: str, candidate: str, src: str, tgt: str) -> dict[str, Any]:
    try:
        from engines.smart_segment_optimizer.quality import validate_optimization

        return validate_optimization(original, candidate, src_lang=src, tgt_lang=tgt)
    except Exception:
        ok = len(candidate) >= max(3, int(len(original) * 0.5))
        return {"ok": ok, "score": 1.0 if ok else 0.3, "issues": [] if ok else ["length"]}


def optimize_translation_text(
    text: str,
    *,
    slot_ms: int,
    src_lang: str = "en",
    tgt_lang: str = "uk",
) -> OptimizerResult:
    """
    TZ Stage 3: shorten without meaning change; rollback on quality drop.
  Falls back to Smart Segment Optimizer when enabled.
    """
    original = (text or "").strip()
    if not original:
        return OptimizerResult(original="", optimized="", changed=False)

    est = _estimate_ms(original, tgt_lang)
    if slot_ms <= 0 or est <= int(slot_ms * 0.92):
        return OptimizerResult(
            original=original,
            optimized=original,
            changed=False,
            quality_before=_quality_check(original, original, src_lang, tgt_lang),
            quality_after=_quality_check(original, original, src_lang, tgt_lang),
        )

    try:
        from engines.smart_segment_optimizer.config import is_enabled as sso_enabled
        from engines.smart_segment_optimizer.optimizer import optimize_segment_text

        if sso_enabled():
            row = optimize_segment_text(
                original,
                segment_ms=slot_ms,
                slot_ms=slot_ms,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                index=0,
            )
            if row and row.optimized:
                return OptimizerResult(
                    original=original,
                    optimized=row.optimized,
                    changed=bool(row.changed),
                    steps=[OptimizerStep("sso", original, row.optimized, True, row.stop_reason)],
                    quality_before=row.quality or {},
                    quality_after=row.quality or {},
                    warnings=[row.stop_reason] if row.overflow else [],
                )
    except Exception:
        pass

    current = original
    steps: list[OptimizerStep] = []
    q_before = _quality_check(original, original, src_lang, tgt_lang)

    for pattern, action in _FILLER_PATTERNS:
        candidate = re.sub(pattern, " ", current, flags=re.I).strip()
        candidate = re.sub(r"\s+", " ", candidate)
        if candidate == current:
            continue
        q = _quality_check(original, candidate, src_lang, tgt_lang)
        accepted = bool(q.get("ok")) and _estimate_ms(candidate, tgt_lang) < est
        steps.append(OptimizerStep(action, current, candidate, accepted, "" if accepted else "quality_drop"))
        if accepted:
            current = candidate
            est = _estimate_ms(current, tgt_lang)

    q_after = _quality_check(original, current, src_lang, tgt_lang)
    warnings: list[str] = []
    if est > slot_ms:
        warnings.append("timing_warning")
    if est > int(slot_ms * 1.15):
        warnings.append("timing_error")

    return OptimizerResult(
        original=original,
        optimized=current,
        changed=current != original,
        steps=steps,
        quality_before=q_before,
        quality_after=q_after,
        warnings=warnings,
    )
