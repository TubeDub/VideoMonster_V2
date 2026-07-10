"""Pass 2 — preserve names, dates, and numbers across translation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})\b"
)
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")
_NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")
# Discourse / function words falsely matched as proper names in English source.
_SKIP_ENTITY_TOKENS = frozenset({
    "but", "as", "the", "a", "an", "and", "or", "if", "when", "while", "then",
    "so", "yet", "for", "nor", "at", "by", "in", "on", "to", "of", "up", "he",
    "she", "it", "we", "they", "his", "her", "its", "our", "their", "this",
    "that", "these", "those", "not", "no", "yes", "all", "any", "some", "each",
    "every", "both", "few", "more", "most", "other", "into", "over", "after",
    "before", "between", "under", "again", "further", "once", "here", "there",
    "where", "why", "how", "what", "which", "who", "whom", "whose", "with",
    "from", "about", "against", "during", "without", "within", "through",
})


@dataclass
class EntityValidationResult:
    ok: bool
    confidence: float
    missing: list[str] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)


def extract_entities(text: str) -> list[str]:
    entities: list[str] = []
    for pattern in (_DATE_RE, _NUMBER_RE, _NAME_RE):
        entities.extend(m.group(0) for m in pattern.finditer(str(text or "")))
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for ent in entities:
        key = ent.lower()
        if key in _SKIP_ENTITY_TOKENS:
            continue
        if key not in seen:
            seen.add(key)
            out.append(ent)
    return out


def validate_entities(source: str, translated: str) -> EntityValidationResult:
    """Check that entities from source appear in translation."""
    entities = extract_entities(source)
    if not entities:
        return EntityValidationResult(ok=True, confidence=1.0)

    tgt = str(translated or "")
    missing: list[str] = []
    preserved: list[str] = []
    for ent in entities:
        if ent in tgt or ent.lower() in tgt.lower():
            preserved.append(ent)
        else:
            missing.append(ent)

    total = len(entities)
    ratio = len(preserved) / total
    return EntityValidationResult(
        ok=len(missing) == 0,
        confidence=round(ratio, 4),
        missing=missing,
        preserved=preserved,
    )
