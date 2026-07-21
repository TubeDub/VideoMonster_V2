"""Meaning / event preservation heuristics (no LLM)."""

from __future__ import annotations

import re

from engines.tqe.rules._registry import register

_EVENT_HINTS = (
    (r"\bcrashed?\b|\bsmashed?\b|\bcollision\b", ("аварі", "зіткн", "розби", "вріза")),
    (r"\bhospital\b|\bintensive care\b", ("лікарн", "реанімац", "інтенсивн")),
    (r"\brace\b|\bracing\b", ("гонк", "трек", "фініш")),
    (r"\bphotograph", ("фото", "камер")),
    (
        r"\bcinematograph|\bfilm school\b|\busc\b",
        ("кінематограф", "кіношкол", "ю ес сі", "usc", "університет"),
    ),
    (r"\bstar wars\b", ("зоряним", "зоряни", "star wars")),
)


@register("meaning")
def check_meaning(original: str, translation: str, ctx: dict) -> list[dict]:
    errors: list[dict] = []
    src = original or ""
    tr = (translation or "").lower()
    if not tr.strip():
        return [{"code": "empty_meaning", "severity": "critical"}]

    src_words = len(src.split())
    tr_words = len(tr.split())
    if src_words >= 25 and tr_words < max(8, int(src_words * 0.25)):
        errors.append(
            {
                "code": "severe_truncation",
                "severity": "critical",
                "detail": f"src_words={src_words} tr_words={tr_words}",
            }
        )

    if re.search(r"\b(not|never|no|didn't|don't|won't)\b", src, re.I):
        if not re.search(r"\b(не|ні|жодн)", tr):
            errors.append({"code": "negation_loss", "severity": "major"})

    hit = 0
    need = 0
    for pat, uk_aliases in _EVENT_HINTS:
        if re.search(pat, src, re.I):
            need += 1
            if any(a in tr for a in uk_aliases):
                hit += 1
            else:
                errors.append(
                    {
                        "code": "event_missing",
                        "detail": pat,
                        "severity": "major",
                    }
                )
    if need and hit == 0 and src_words >= 12:
        errors.append(
            {
                "code": "meaning_collapse",
                "severity": "critical",
                "detail": "no mapped events preserved",
            }
        )
    return errors
