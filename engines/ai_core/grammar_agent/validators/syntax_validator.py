"""Syntax validation — structure and duplicate tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SyntaxValidationResult:
    ok: bool
    score: float
    issues: list[str] = field(default_factory=list)


def validate_syntax(text: str) -> SyntaxValidationResult:
    cand = str(text or "").strip()
    issues: list[str] = []

    if not cand:
        return SyntaxValidationResult(False, 0.0, ["empty"])

    if re.search(r"\b(\w+)\s+\1\b", cand, re.IGNORECASE):
        issues.append("duplicate_word")

    if re.search(r"\s{2,}", cand):
        issues.append("extra_spaces")

    if re.search(r"\s+([,.!?;:])", cand):
        issues.append("space_before_punct")

    score = 1.0 if not issues else max(0.0, 1.0 - 0.2 * len(issues))
    return SyntaxValidationResult(ok=score >= 0.7, score=round(score, 4), issues=issues)
