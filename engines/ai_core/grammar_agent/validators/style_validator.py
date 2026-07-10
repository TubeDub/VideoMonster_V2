"""Style validation — readability without length drift."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class StyleValidationResult:
    ok: bool
    score: float
    issues: list[str] = field(default_factory=list)


def validate_style(reference: str, candidate: str) -> StyleValidationResult:
    ref = str(reference or "").strip()
    cand = str(candidate or "").strip()
    issues: list[str] = []

    if not cand:
        return StyleValidationResult(False, 0.0, ["empty"])

    stiff = [
        r"\bданн(?:ый|ая|ое|ые)\b",
        r"\bв связи с\b",
        r"\bСледует отметить\b",
    ]
    for pat in stiff:
        if re.search(pat, cand, re.IGNORECASE):
            issues.append("stiff_phrasing")

    if ref:
        ratio = len(cand) / max(len(ref), 1)
        if ratio < 0.85 or ratio > 1.15:
            issues.append("length_drift")

    score = 1.0 if not issues else max(0.0, 1.0 - 0.15 * len(issues))
    return StyleValidationResult(ok=score >= 0.7, score=round(score, 4), issues=issues)
