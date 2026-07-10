"""Grammar quality heuristics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class GrammarValidationResult:
    ok: bool
    score: float
    issues: list[str] = field(default_factory=list)


def validate_grammar(text: str, *, tgt_lang: str = "ru") -> GrammarValidationResult:
    cand = str(text or "").strip()
    issues: list[str] = []

    if not cand:
        return GrammarValidationResult(False, 0.0, ["empty"])

    bad_patterns = [
        (r"\bсказал что\b", "missing_comma_after_said"),
        (r"\bпотому что что\b", "duplicate_que"),
        (r"\b(?:он|она|они)\s+есть\b", "literal_is"),
        (r"\bявляется\b", "stiff_copula"),
        (r"\bосуществляет\b", "stiff_verb"),
        (r"\bв настоящее время\b", "literal_now"),
    ]
    for pat, code in bad_patterns:
        if re.search(pat, cand, re.IGNORECASE):
            issues.append(code)

    if cand and not re.search(r"[.!?…]$", cand):
        issues.append("missing_terminal_punct")

    score = 1.0 if not issues else max(0.0, 1.0 - 0.12 * len(issues))
    return GrammarValidationResult(ok=score >= 0.75, score=round(score, 4), issues=issues)
