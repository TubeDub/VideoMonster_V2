"""P206 Entity Preservation + P207 Terminology Manager."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_NUM = re.compile(r"\d[\d.,]*")
_CURRENCY = re.compile(r"[$€£₴¥]|USD|EUR|UAH|GBP", re.I)
_DATE = re.compile(
    r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})\b",
    re.I,
)


@dataclass
class TerminologyManager:
    project_terms: dict[str, str] = field(default_factory=dict)
    user_terms: dict[str, str] = field(default_factory=dict)
    industry_terms: dict[str, str] = field(default_factory=dict)
    exceptions: set[str] = field(default_factory=set)

    def all_terms(self) -> dict[str, str]:
        out = dict(self.industry_terms)
        out.update(self.user_terms)
        out.update(self.project_terms)
        return out

    def apply(self, text: str) -> str:
        out = text
        for src, tgt in self.all_terms().items():
            if not src or src.lower() in self.exceptions:
                continue
            out = re.sub(rf"\b{re.escape(src)}\b", tgt, out, flags=re.I)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_terms": dict(self.project_terms),
            "user_terms": dict(self.user_terms),
            "industry_terms": dict(self.industry_terms),
            "exceptions": sorted(self.exceptions),
        }


def extract_protected_tokens(source: str, entities: list[str] | None = None) -> list[str]:
    """Names, numbers, dates, currencies, brands — must survive translation."""
    skip = {
        "i", "a", "an", "the", "and", "or", "but", "to", "of", "in", "on", "for",
        "he", "she", "it", "they", "his", "her", "their", "this", "that",
        "я", "і", "та", "але", "на", "у", "в", "з", "від",
    }
    protected: list[str] = []
    protected.extend(_NUM.findall(source or ""))
    protected.extend(m.group(0) for m in _DATE.finditer(source or ""))
    protected.extend(m.group(0) for m in _CURRENCY.finditer(source or ""))
    for e in entities or []:
        if e and e not in protected and e.lower() not in skip:
            protected.append(e)
    for tok in re.findall(r"\b[A-ZА-ЯЁІЇЄҐ][\w'’.-]{1,}\b", source or ""):
        if tok.lower() in skip:
            continue
        if len(tok) <= 1:
            continue
        if tok not in protected:
            protected.append(tok)
    return list(dict.fromkeys(protected))


def entity_preservation_score(
    source: str,
    translated: str,
    entities: list[str] | None = None,
) -> float:
    protected = extract_protected_tokens(source, entities)
    if not protected:
        return 1.0
    low = (translated or "").lower()
    ok = 0
    for p in protected:
        # Numbers/dates must appear literally; names case-insensitive
        if _NUM.fullmatch(p) or _DATE.fullmatch(p):
            if p in (translated or ""):
                ok += 1
        elif p.lower() in low or p in (translated or ""):
            ok += 1
    return ok / max(1, len(protected))


def assert_entities_preserved(
    source: str,
    translated: str,
    entities: list[str] | None = None,
) -> None:
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    score = entity_preservation_score(source, translated, entities)
    if score < 1.0 - 1e-9:
        missing = [
            p
            for p in extract_protected_tokens(source, entities)
            if p not in (translated or "") and p.lower() not in (translated or "").lower()
        ]
        raise ArchitectureViolation(
            "P206 Entity Preservation failed",
            stage="translation_core",
            rule="entity_preservation",
            details={"missing": missing[:8], "score": score},
        )
