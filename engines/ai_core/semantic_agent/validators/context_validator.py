"""Context validation — logical links preserved."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CONNECTORS = re.compile(
    r"\b(?:and|but|or|however|although|while|then|after|before|when|if|"
    r"и|но|или|однако|хотя|пока|потом|после|до|когда|если|"
    r"і|але|або|однак|хоча|поки|після|до|коли|якщо)\b",
    re.IGNORECASE,
)
_PRONOUN_LINKS = re.compile(
    r"\b(?:he|she|they|it|this|that|these|those|"
    r"он|она|они|оно|этот|эта|это|эти|"
    r"він|вона|вони|воно|цей|ця|це|ці)\b",
    re.IGNORECASE,
)


@dataclass
class ContextValidationResult:
    ok: bool
    score: float
    connector_preserved: bool = True
    pronoun_links: bool = True
    issues: list[str] = field(default_factory=list)


def _preserve_ratio(source: str, candidate: str, pattern: re.Pattern) -> tuple[float, bool]:
    hits = [m.group(0).lower() for m in pattern.finditer(source or "")]
    if not hits:
        return 1.0, True
    cand = (candidate or "").lower()
    preserved = sum(1 for h in hits if h in cand)
    ratio = preserved / len(hits)
    return ratio, ratio >= 0.4


def validate_context(
    source: str,
    translated: str,
    candidate: str,
    *,
    prev_context: str | None = None,
) -> ContextValidationResult:
    """Ensure logical connectors and discourse links are preserved."""
    conn_score, conn_ok = _preserve_ratio(translated, candidate, _CONNECTORS)
    pron_score, pron_ok = _preserve_ratio(translated, candidate, _PRONOUN_LINKS)

    continuity = 1.0
    if prev_context and candidate:
        prev_words = set(re.findall(r"\w+", prev_context.lower())[-3:])
        cand_words = set(re.findall(r"\w+", candidate.lower())[:5])
        if prev_words and cand_words:
            continuity = len(prev_words & cand_words) / max(len(prev_words), 1)
            continuity = min(1.0, 0.5 + continuity)

    score = round(0.40 * conn_score + 0.35 * pron_score + 0.25 * continuity, 4)
    issues: list[str] = []
    if not conn_ok:
        issues.append("connectors_weakened")
    if not pron_ok:
        issues.append("pronoun_links_weakened")

    return ContextValidationResult(
        ok=score >= 0.6,
        score=score,
        connector_preserved=conn_ok,
        pronoun_links=pron_ok,
        issues=issues,
    )
