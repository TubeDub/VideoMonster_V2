"""P106 Sentence Confidence + P103 sentence typing helpers."""

from __future__ import annotations

import os
import re

from engines.semantic_v3.types import SemanticSentence

_CONF_THRESHOLD = float(os.environ.get("VM_SENTENCE_CONFIDENCE_MIN", "0.70"))


def classify_sentence_type(sent: SemanticSentence) -> str:
    text = (sent.text or "").strip()
    sent.is_question = text.endswith("?")
    sent.is_exclamation = text.endswith("!")
    sent.is_incomplete = (
        len(text.split()) <= 2 and not text.endswith((".", "!", "?"))
    ) or text.endswith(("...", "…", ","))
    if sent.is_enumeration:
        sent.sentence_type = "enumeration"
    elif sent.is_subordinate:
        sent.sentence_type = "subordinate"
    elif sent.is_complex and "," in text and re.search(r"\b(and|but|or|але|і|та)\b", text, re.I):
        sent.sentence_type = "compound"
    elif sent.is_complex:
        sent.sentence_type = "complex"
    elif sent.is_direct_speech:
        sent.sentence_type = "direct_speech"
    elif sent.is_question:
        sent.sentence_type = "question"
    elif sent.is_exclamation:
        sent.sentence_type = "exclamation"
    elif sent.has_address:
        sent.sentence_type = "address"
    elif sent.has_parenthetical:
        sent.sentence_type = "parenthetical"
    elif sent.is_incomplete:
        sent.sentence_type = "incomplete"
    else:
        sent.sentence_type = "simple"
    return sent.sentence_type


def compute_sentence_confidence(sent: SemanticSentence) -> float:
    """P106 — deterministic confidence in [0,1]."""
    score = 1.0
    if not sent.words:
        score -= 0.4
    if sent.is_incomplete:
        score -= 0.25
    if not sent.text.strip().endswith((".", "!", "?", "…", '"', "»")):
        if len(sent.words) > 4:
            score -= 0.1
    # Low ASR word confidence
    if sent.words:
        avg = sum(float(w.confidence or 1.0) for w in sent.words) / len(sent.words)
        score = min(score, 0.55 + 0.45 * avg)
    # Missing structure for long sentence
    if len(sent.words) >= 20 and not sent.verbs:
        score -= 0.15
    sent.sentence_confidence = max(0.0, min(1.0, round(score, 3)))
    return sent.sentence_confidence


def apply_sentence_confidence(
    sentences: list[SemanticSentence],
    *,
    threshold: float | None = None,
) -> list[SemanticSentence]:
    thr = _CONF_THRESHOLD if threshold is None else threshold
    for s in sentences:
        classify_sentence_type(s)
        compute_sentence_confidence(s)
        if s.sentence_confidence < thr:
            s.semantic_status = "needs_review"
            s.recovery_plan = list(
                dict.fromkeys([*(s.recovery_plan or []), "reanalyze_sentence"])
            )
        elif s.semantic_status == "raw":
            s.semantic_status = "analyzed"
    return sentences


def confidence_threshold() -> float:
    return _CONF_THRESHOLD
