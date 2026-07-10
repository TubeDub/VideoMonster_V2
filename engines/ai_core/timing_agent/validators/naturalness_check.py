"""Naturalness check for timing-adapted text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class NaturalnessResult:
    ok: bool
    score: float
    issues: list[str] = field(default_factory=list)


_BAD_PATTERNS = [
    r"\b(?:является|осуществляет|данный)\b",
    r"\b(?:здійснює|даний)\b",
    r"(.)\1{3,}",  # repeated char spam
]


def validate_naturalness(original: str, candidate: str) -> NaturalnessResult:
    cand = str(candidate or "").strip()
    orig = str(original or "").strip()
    if not cand:
        return NaturalnessResult(False, 0.0, ["empty"])

    score = 0.7
    issues: list[str] = []

    for pat in _BAD_PATTERNS:
        if re.search(pat, cand, re.IGNORECASE):
            score -= 0.12
            issues.append("artifact")

    # Penalize unnatural word repetition to fill slot
    words = cand.lower().split()
    if len(words) >= 4:
        for w in set(words):
            if words.count(w) >= 3 and len(w) > 2:
                score -= 0.25
                issues.append("word_repeat_fill")
                break

    if cand != orig and re.search(r"[.!?]", cand):
        score += 0.08

    score = round(max(0.0, min(1.0, score)), 4)
    return NaturalnessResult(ok=score >= 0.55, score=score, issues=issues)
