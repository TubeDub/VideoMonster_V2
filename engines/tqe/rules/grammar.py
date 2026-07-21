"""Grammar / fragment glue rejection rules."""

from __future__ import annotations

import re

from engines.tqe.rules._registry import register

# Constructions that must never reach TTS (TZ Grammar Reviewer)
_BAD_FRAGMENTS = (
    re.compile(r"(^|[,;]\s*)справжню роботу\.?\s*$", re.I),
    re.compile(r"(^|[,;]\s*)досвід на межі смерті\.?\s*$", re.I),
    re.compile(r"\bне довге\b", re.I),
    re.compile(r"\bвистачить,\s*не довге\b", re.I),
    re.compile(r",\s*справжню роботу\.?\s*$", re.I),
    re.compile(r",\s*досвід на межі смерті\.?\s*$", re.I),
)


@register("grammar")
def check_grammar(original: str, translation: str, ctx: dict) -> list[dict]:
    errors: list[dict] = []
    text = (translation or "").strip()
    if not text:
        return [{"code": "empty", "severity": "critical", "detail": "empty translation"}]

    for pat in _BAD_FRAGMENTS:
        if pat.search(text):
            errors.append(
                {
                    "code": "orphan_clause_glue",
                    "detail": pat.pattern[:80],
                    "severity": "critical",
                }
            )

    # Fake opener only when EN does not open with the same clause
    src = (original or "").strip()
    if re.match(r"^між\s+батьком\s+і\s+сином\b", text, re.I) and not re.match(
        r"^between\s+father\s+and\s+son\b", src, re.I
    ):
        errors.append(
            {
                "code": "orphan_clause_prefix",
                "detail": "father-son glue used as false opener",
                "severity": "critical",
            }
        )

    if re.search(r"\.\s+[а-яіїєґa-z]", text):
        if text.count(". ") >= 2 and re.search(r"\.\s+[а-яіїєґ]", text):
            errors.append(
                {
                    "code": "broken_sentence_boundary",
                    "severity": "major",
                    "detail": "lowercase after period",
                }
            )

    if re.search(r",\s*[а-яіїєґ]{3,20}\.?\s*$", text) and len(text.split()) < 8:
        errors.append(
            {
                "code": "trailing_fragment",
                "severity": "major",
                "detail": "looks like clause glue fragment",
            }
        )
    return errors
