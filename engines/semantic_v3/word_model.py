"""P101 — Word Model enrichment (atomic speech units)."""

from __future__ import annotations

import re
from typing import Iterable

from engines.semantic_v3.types import SemanticWord

_LEMMA_STRIP = re.compile(r"[^\w'’-]+", re.UNICODE)


def enrich_word_model(
    words: Iterable[SemanticWord],
    *,
    language: str = "en",
    scene_uuid: str = "",
    paragraph_uuid: str = "",
) -> list[SemanticWord]:
    """Fill P101 fields deterministically. Whisper remains timestamp source only."""
    out = list(words)
    for w in out:
        w.language = language or w.language
        w.normalized_text = w.normalized_text or _LEMMA_STRIP.sub("", w.text).lower()
        w.lemma = w.lemma or w.normalized_text
        w.scene_uuid = scene_uuid or w.scene_uuid
        w.paragraph_uuid = paragraph_uuid or w.paragraph_uuid
        w.speaker_uuid = w.speaker_uuid or w.speaker
        w.entity_type = w.entity_type or w.entity
        w.importance_score = w.importance_score if w.importance_score != 0.5 else w.importance
        if w.entity_type and not w.entity_id:
            w.entity_id = f"ent:{w.normalized_text}"
        if not w.prosody:
            if w.pause_after_ms >= 300:
                w.prosody = "pause"
            elif w.stress > 0.6:
                w.prosody = "stressed"
            else:
                w.prosody = "neutral"
    return out


def assert_word_model_complete(words: list[SemanticWord]) -> None:
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    for w in words:
        if not w.word_uuid or not (w.text or "").strip():
            raise ArchitectureViolation(
                "P101 incomplete Word (uuid/text)",
                stage="word_model",
                rule="word_atomic",
                details={"word": getattr(w, "text", "")},
            )
        if int(w.end_ms) < int(w.start_ms):
            raise ArchitectureViolation(
                "P101 Word timing invalid",
                stage="word_model",
                rule="word_atomic",
                details={"word": w.text},
            )
