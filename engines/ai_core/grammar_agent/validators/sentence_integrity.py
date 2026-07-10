"""No broken words, no truncation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engines.semantic_meaning import is_truncated_adaptation


@dataclass
class SentenceIntegrityResult:
    ok: bool
    score: float
    issues: list[str] = field(default_factory=list)


def validate_sentence_integrity(original: str, candidate: str) -> SentenceIntegrityResult:
    cand = str(candidate or "").strip()
    orig = str(original or "").strip()
    issues: list[str] = []

    if not cand:
        return SentenceIntegrityResult(False, 0.0, ["empty"])

    if "..." in cand or cand.endswith("…"):
        issues.append("ellipsis")

    if re.search(r"\w+-\s*$", cand):
        issues.append("hyphen_truncation")

    if is_truncated_adaptation(orig, cand):
        issues.append("truncated_tail")

    if orig and len(cand) < len(orig) * 0.7:
        last = cand.split()[-1] if cand.split() else ""
        if last and not re.search(r"[.!?…,;:]$", cand) and len(last) < 3:
            issues.append("suspect_word_cut")

    score = 1.0 if not issues else max(0.0, 1.0 - 0.35 * len(issues))
    return SentenceIntegrityResult(ok=not issues, score=round(score, 4), issues=issues)
