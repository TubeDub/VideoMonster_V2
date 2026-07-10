"""Voice readiness — text not empty, pronounceable, no triple consonants."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engines.ai_core.grammar_agent.pronunciation_optimizer import fix_triple_consonants

_TRIPLE_CONSONANT_RE = re.compile(
    r"([бвгджзклмнпрстфхцчшщbcdfghjklmnpqrstvwxyz])\1{2,}",
    re.IGNORECASE,
)
_UNPRONOUNCEABLE_RE = re.compile(r"[^\w\s\.,!?;:'\"()\-\u0400-\u04FF\u00C0-\u024F\u200b]", re.UNICODE)


@dataclass
class CheckResult:
    ok: bool
    score: float
    failure_type: str | None = None
    issues: list[str] = field(default_factory=list)


def check_voice_readiness(text: str) -> CheckResult:
    cand = str(text or "").strip()
    issues: list[str] = []

    if not cand:
        return CheckResult(False, 0.0, "voice_readiness", ["empty"])

    if cand.upper() in ("NULL", "NONE", "N/A"):
        return CheckResult(False, 0.0, "voice_readiness", ["null_literal"])

    if _TRIPLE_CONSONANT_RE.search(cand):
        issues.append("triple_consonants")

    garbage = len(_UNPRONOUNCEABLE_RE.findall(cand))
    if garbage > max(2, len(cand) // 25):
        issues.append("unpronounceable_chars")

    # Soft hint: fixed text would differ — still warn but not block if minor
    fixed = fix_triple_consonants(cand)
    if fixed != cand and "triple_consonants" not in issues:
        issues.append("pronunciation_clusters")

    score = 1.0 if not issues else max(0.0, 1.0 - 0.25 * len(issues))
    ok = score >= 0.7 and "empty" not in issues and "null_literal" not in issues
    return CheckResult(
        ok=ok,
        score=round(score, 4),
        failure_type=None if ok else "voice_readiness",
        issues=issues,
    )
