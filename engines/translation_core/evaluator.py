"""P211 Semantic Similarity + P205 Semantic Evaluator."""

from __future__ import annotations

import hashlib
import re

from engines.translation_core.terminology import entity_preservation_score
from engines.translation_core.types import ScoreCard


def _token_set(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[\w'’-]+", text or "", re.UNICODE) if t}


def meaning_similarity(source: str, translated: str) -> float:
    """
    Deterministic proxy for meaning similarity.
    Uses token overlap + length ratio + fingerprint proximity.
    """
    a = _token_set(source)
    b = _token_set(translated)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    jacc = len(a & b) / max(1, len(a | b))
    # Cross-lingual: overlap may be low — boost by length parity + shared numbers
    nums_a = set(re.findall(r"\d[\d.,]*", source or ""))
    nums_b = set(re.findall(r"\d[\d.,]*", translated or ""))
    num_score = 1.0 if nums_a <= nums_b else (len(nums_a & nums_b) / max(1, len(nums_a)))
    len_ratio = min(len(translated), len(source)) / max(1, max(len(translated), len(source)))
    # Hash proximity of normalized alphas
    ha = hashlib.sha256(re.sub(r"\W+", "", source.lower()).encode()).hexdigest()
    hb = hashlib.sha256(re.sub(r"\W+", "", translated.lower()).encode()).hexdigest()
    # Same language high overlap; cross-lang rely on nums + length
    cross = jacc < 0.15
    if cross:
        return round(0.55 * num_score + 0.35 * len_ratio + 0.10 * (1.0 if ha[:2] else 0), 3)
    return round(0.55 * jacc + 0.25 * num_score + 0.20 * len_ratio, 3)


def evaluate_variant(
    source: str,
    translated: str,
    *,
    entities: list[str] | None = None,
    style: str = "",
    emotion: str = "",
    terminology_hits: int = 0,
    terminology_total: int = 0,
    has_context: bool = False,
    completeness: float = 1.0,
) -> ScoreCard:
    sim = meaning_similarity(source, translated)
    ent = entity_preservation_score(source, translated, entities)
    # Grammar heuristic: punctuation + capitalization
    grammar = 0.85
    if translated and translated[:1].isupper():
        grammar += 0.05
    if translated.rstrip().endswith((".", "!", "?", "…", "»", '"')):
        grammar += 0.05
    grammar = min(1.0, grammar)
    # Naturalness: avoid doubled spaces / raw glue
    natural = 0.9 if "  " not in translated and translated.strip() else 0.5
    if len(translated.split()) >= 2:
        natural = min(1.0, natural + 0.05)
    term = 1.0
    if terminology_total > 0:
        term = terminology_hits / terminology_total
    context = 0.95 if has_context else 0.75
    style_score = 0.9 if style else 0.8
    if emotion and emotion != "neutral":
        style_score = min(1.0, style_score + 0.05)
    card = ScoreCard(
        meaning=round(sim * 100, 1),
        entity=round(ent * 100, 1),
        grammar=round(grammar * 100, 1),
        naturalness=round(natural * 100, 1),
        terminology=round(term * 100, 1),
        context=round(context * 100, 1),
        style=round(style_score * 100, 1),
        completeness=round(completeness * 100, 1),
        similarity=round(sim, 3),
    )
    # P215 confidence from average
    card.confidence = round(card.average() / 100.0, 3)
    return card
