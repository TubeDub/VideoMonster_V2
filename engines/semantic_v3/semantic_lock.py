"""P7 — Semantic Lock (meaning/entities/numbers — not raw Text Lock alone)."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.types import SemanticSentence

_NUM = re.compile(r"\d[\d.,]*")


def _fingerprint(sent: SemanticSentence) -> str:
    payload = "|".join(
        [
            ",".join(sorted(e.lower() for e in sent.entities)),
            ",".join(_NUM.findall(sent.text)),
            sent.intent,
            (sent.translated_text or sent.text)[:80].lower(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def lock_sentence(sent: SemanticSentence) -> SemanticSentence:
    sent.locked_entities = list(sent.entities)
    sent.locked_numbers = _NUM.findall(sent.text) + _NUM.findall(sent.translated_text or "")
    sent.meaning_fingerprint = _fingerprint(sent)
    sent.semantic_locked = True
    return sent


def lock_all(sentences: list[SemanticSentence]) -> list[SemanticSentence]:
    return [lock_sentence(s) for s in sentences]


def assert_semantic_rewrite_allowed(
    before: SemanticSentence,
    after_text: str,
    *,
    meaning_similarity: float,
    entity_preservation: float,
    threshold: float = 0.85,
) -> None:
    """P7 — rewrite OK only if meaning ≥ threshold and entities 100%."""
    if not before.semantic_locked:
        return
    if entity_preservation < 1.0 - 1e-9:
        raise ArchitectureViolation(
            "P7 Semantic Lock: entity preservation must be 100%",
            stage="semantic_lock",
            rule="entity_preservation",
            segment_id=before.sentence_uuid,
            details={"entity_preservation": entity_preservation},
        )
    if meaning_similarity < threshold:
        raise ArchitectureViolation(
            f"P7 Semantic Lock: meaning similarity {meaning_similarity:.3f} < {threshold}",
            stage="semantic_lock",
            rule="meaning_similarity",
            segment_id=before.sentence_uuid,
        )
    # Numbers must survive
    for n in before.locked_numbers:
        if n and n not in after_text:
            raise ArchitectureViolation(
                f"P7 Semantic Lock: number {n!r} removed",
                stage="semantic_lock",
                rule="numbers",
                segment_id=before.sentence_uuid,
            )


def entity_preservation_score(before: SemanticSentence, after_text: str) -> float:
    if not before.locked_entities and not before.entities:
        return 1.0
    ents = before.locked_entities or before.entities
    if not ents:
        return 1.0
    low = after_text.lower()
    ok = sum(1 for e in ents if e.lower() in low)
    return ok / max(1, len(ents))


def apply_locked_translation(sent: SemanticSentence, translated: str) -> SemanticSentence:
    """Attach translation then lock (P5→P7 path)."""
    sent.translated_text = " ".join(str(translated or "").split())
    return lock_sentence(sent)
