"""Natural speech validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class NaturalSpeechValidationResult:
    ok: bool
    score: float
    issues: list[str] = field(default_factory=list)


def validate_natural_speech(text: str, *, tgt_lang: str = "ru") -> NaturalSpeechValidationResult:
    cand = str(text or "").strip()
    issues: list[str] = []

    if not cand:
        return NaturalSpeechValidationResult(False, 0.0, ["empty"])

    calques = [
        r"\bв настоящее время\b",
        r"\bв данный момент\b",
        r"\bделает так что\b",
        r"\bон есть\b",
        r"\bв даний час\b",
        r"\bу зв'язку з тим що\b",
    ]
    for pat in calques:
        if re.search(pat, cand, re.IGNORECASE):
            issues.append("literal_calque")

    score = 1.0 if not issues else max(0.0, 1.0 - 0.18 * len(issues))
    return NaturalSpeechValidationResult(ok=score >= 0.7, score=round(score, 4), issues=issues)
