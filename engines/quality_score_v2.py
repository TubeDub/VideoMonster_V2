"""Quality Score v2 — multi-dimensional dubbing quality (Production TZ §16).

Extends existing scoring without breaking callers of compute_quality_score.
"""

from __future__ import annotations

import re
from typing import Any

from engines.translation_quality_score import compute_quality_metrics, compute_quality_score

_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_ENTITY_RE = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\b")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or ""))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a | b), 1)


def _entity_tokens(text: str) -> set[str]:
    return {m.group(1).lower() for m in _ENTITY_RE.finditer(str(text or ""))}


def _numbers(text: str) -> set[str]:
    return set(_NUM_RE.findall(str(text or "")))


def compute_quality_score_v2(
    original: str,
    translated: str,
    *,
    src_lang: str | None = None,
    tgt_lang: str | None = None,
    naturalized: str | None = None,
) -> tuple[float, dict[str, Any]]:
    """Return (0–100 score, dimensions dict).

    Dimensions (TZ §16):
      Semantic Similarity, Entity Preservation, Hallucination Detection,
      Naturalness, Grammar, Fluency, Event Preservation, Compression,
      Causal links (proxy), Emotions (proxy).
    """
    base_score, base = compute_quality_score(
        original, translated, src_lang=src_lang, tgt_lang=tgt_lang
    )
    metrics = compute_quality_metrics(
        original, translated, src_lang=src_lang, tgt_lang=tgt_lang
    )
    text = str(naturalized or translated or "").strip()
    orig = str(original or "").strip()
    ow, tw = _words(orig), _words(text)

    # Entity preservation
    o_ent, t_ent = _entity_tokens(orig), _entity_tokens(text)
    entity_preservation = _jaccard(o_ent, t_ent) if o_ent else 1.0

    # Numbers / events proxy
    o_num, t_num = _numbers(orig), _numbers(text)
    event_preservation = _jaccard(o_num, t_num) if o_num else 1.0

    # Compression vs original length (word ratio capped)
    compression = 1.0
    if ow:
        ratio = len(tw) / max(len(ow), 1)
        # Ideal band ~0.7–1.3 for dubbing
        if ratio < 0.5:
            compression = max(0.0, ratio / 0.5)
        elif ratio > 1.6:
            compression = max(0.0, 1.0 - (ratio - 1.6) / 1.0)
        else:
            compression = 1.0

    # Hallucination: latin junk in cyrillic targets / extreme length growth
    hallucination = 1.0
    if metrics.get("english_word_pct", 0) > 25:
        hallucination -= 0.35
    if metrics.get("cjk_garbage"):
        hallucination -= 0.5
    if metrics.get("placeholder_leak_count", 0) > 0:
        hallucination -= 0.4
    hallucination = max(0.0, hallucination)

    # Naturalness / grammar / fluency proxies from existing metrics
    naturalness = max(0.0, 1.0 - float(metrics.get("mixed_language_pct", 0) or 0) / 100.0)
    grammar = max(0.0, float(metrics.get("translated_pct", 100) or 100) / 100.0)
    fluency = naturalness * 0.6 + grammar * 0.4

    # Semantic similarity proxy: inverse of shortening + entity + numbers
    shortening = abs(1.0 - (len(tw) / max(len(ow), 1))) if ow else 0.0
    semantic_similarity = max(
        0.0,
        1.0
        - shortening * 0.35
        - (1.0 - entity_preservation) * 0.4
        - (1.0 - event_preservation) * 0.25,
    )

    # Causal / emotion lightweight proxies (content words overlap)
    o_set = {w.lower() for w in ow if len(w) > 3}
    t_set = {w.lower() for w in tw if len(w) > 3}
    causal = _jaccard(o_set, t_set) * 0.5 + event_preservation * 0.5
    emotion = 1.0  # reserved for future LI emotion tags; neutral default

    dims = {
        "semantic_similarity": round(semantic_similarity, 3),
        "entity_preservation": round(entity_preservation, 3),
        "hallucination_detection": round(hallucination, 3),
        "naturalness": round(naturalness, 3),
        "grammar": round(grammar, 3),
        "fluency": round(fluency, 3),
        "event_preservation": round(event_preservation, 3),
        "compression": round(compression, 3),
        "causal_links": round(causal, 3),
        "emotions": round(emotion, 3),
    }
    weights = {
        "semantic_similarity": 0.18,
        "entity_preservation": 0.14,
        "hallucination_detection": 0.14,
        "naturalness": 0.10,
        "grammar": 0.08,
        "fluency": 0.08,
        "event_preservation": 0.10,
        "compression": 0.08,
        "causal_links": 0.05,
        "emotions": 0.05,
    }
    composite = sum(dims[k] * weights[k] for k in weights) * 100.0
    # Blend with legacy score so thresholds stay familiar.
    final = 0.55 * float(base_score) + 0.45 * composite
    details = {
        **base,
        **metrics,
        "dimensions": dims,
        "legacy_score": round(float(base_score), 2),
        "v2_composite": round(composite, 2),
        "quality_score": round(final, 2),
        "score_version": 2,
    }
    return round(final, 2), details
