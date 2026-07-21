"""P210 Adaptive Translation + P214 Rewrite Engine (pre-lock only)."""

from __future__ import annotations

import re

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.translation_core.evaluator import meaning_similarity
from engines.translation_core.terminology import entity_preservation_score

_REWRITE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bI am going to\b", re.I), "I'll"),
    (re.compile(r"\bI will\b", re.I), "I'll"),
    (re.compile(r"\bdo not\b", re.I), "don't"),
    (re.compile(r"\bdid not\b", re.I), "didn't"),
    (re.compile(r"\bcannot\b", re.I), "can't"),
    (re.compile(r"\bit is\b", re.I), "it's"),
    (re.compile(r"\bв зв'язку з тим, що\b", re.I), "тому що"),
    (re.compile(r"\bу той момент, коли\b", re.I), "коли"),
    (re.compile(r"\bпісля цього\b", re.I), "потім"),
    (re.compile(r"\bнадзвичайно\b", re.I), "дуже"),
    (re.compile(r"\bнасправді\b", re.I), ""),
    (re.compile(r"\ba lot of\b", re.I), "many"),
    (re.compile(r"\bin order to\b", re.I), "to"),
]


def adaptive_rewrite(text: str) -> str:
    """Localizing reformulation — not mechanical chopping."""
    out = " ".join(str(text or "").split())
    for pat, repl in _REWRITE_RULES:
        out = pat.sub(repl, out)
    return " ".join(out.split())


def make_variant_seeds(source: str) -> list[tuple[str, str]]:
    """
    Produce (label, source_seed) for multi-pass.
    A = original, B = adaptive, C = more compressed adaptive, D = style-neutral trim.
    """
    a = " ".join(str(source or "").split())
    b = adaptive_rewrite(a)
    c = adaptive_rewrite(b)
    # D: drop filler commas doubles
    d = re.sub(r"\s*,\s*", ", ", c)
    d = re.sub(r"\b(really|actually|basically|just)\b", "", d, flags=re.I)
    d = " ".join(d.split())
    return [("A", a), ("B", b), ("C", c), ("D", d or a)]


def safe_rewrite(
    source: str,
    candidate: str,
    *,
    entities: list[str] | None = None,
    locked: bool = False,
    min_similarity: float = 0.85,
) -> str:
    """P214 — rewrite only before lock; must preserve meaning/entities."""
    if locked:
        raise ArchitectureViolation(
            "P214/P216: rewrite forbidden after Semantic Lock",
            stage="translation_core",
            rule="post_lock_no_rewrite",
        )
    rewritten = adaptive_rewrite(candidate)
    if entity_preservation_score(source, rewritten, entities) < 1.0 - 1e-9:
        return candidate
    if meaning_similarity(source, rewritten) < min_similarity and meaning_similarity(
        candidate, rewritten
    ) < 0.7:
        return candidate
    return rewritten
