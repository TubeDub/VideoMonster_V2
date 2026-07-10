"""Pass 3 — consistent terminology across project segments."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_TERM_RE = re.compile(r"\b[A-Z][A-Za-z]{2,}\b")


@dataclass
class TerminologyValidationResult:
    ok: bool
    confidence: float
    inconsistent_terms: list[str] = field(default_factory=list)
    glossary_size: int = 0


def build_glossary(segments: list[dict], *, min_freq: int = 2) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for seg in segments:
        text = str(seg.get("text") or "")
        for term in _TERM_RE.findall(text):
            if len(term) > 2:
                counter[term] += 1
    return {t: c for t, c in counter.items() if c >= min_freq}


def validate_terminology(
    segments: list[dict],
    glossary: dict[str, int] | None = None,
) -> TerminologyValidationResult:
    """Ensure repeated source terms map to one translation variant."""
    glossary = glossary or build_glossary(segments)
    if not glossary:
        return TerminologyValidationResult(ok=True, confidence=1.0, glossary_size=0)

    inconsistent: list[str] = []
    checked = 0
    ok_count = 0

    for term in glossary:
        variants: set[str] = set()
        for seg in segments:
            src = str(seg.get("text") or "")
            if term not in src:
                continue
            translated = str(seg.get("translated_text") or "")
            if not translated:
                continue
            # Heuristic: token after term position or whole-segment fingerprint
            idx = src.find(term)
            if idx >= 0:
                snippet = translated[max(0, idx - 5) : idx + len(term) + 20].strip()
                if snippet:
                    variants.add(snippet[:40])
        if len(variants) <= 1:
            ok_count += 1
        else:
            inconsistent.append(term)
        checked += 1

    confidence = ok_count / checked if checked else 1.0
    return TerminologyValidationResult(
        ok=len(inconsistent) == 0,
        confidence=round(confidence, 4),
        inconsistent_terms=inconsistent,
        glossary_size=len(glossary),
    )
