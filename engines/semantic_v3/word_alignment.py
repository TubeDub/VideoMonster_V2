"""P35 Word Alignment Engine — full word objects bound to sentences."""

from __future__ import annotations

import re
import uuid
from typing import Any

from engines.semantic_v3.phoneme_viseme import enrich_word_articulation
from engines.semantic_v3.types import SemanticSentence, SemanticWord

_LEMMA_STRIP = re.compile(r"[^\w'’-]+", re.UNICODE)


def align_words_to_sentences(
    sentences: list[SemanticSentence],
    *,
    language: str = "en",
    speech_rate: float = 1.0,
) -> list[SemanticSentence]:
    """Ensure every word has P35 fields + phoneme/viseme articulation."""
    for s in sentences:
        for w in s.words:
            if not w.word_uuid:
                w.word_uuid = uuid.uuid4().hex
            lemma = _LEMMA_STRIP.sub("", w.text).lower()
            w.lemma = lemma
            w.language = language
            w.sentence_uuid = s.sentence_uuid
            w.speaker_uuid = s.speaker or ""
            w.dependency = "root" if w is s.words[0] else "dep"
            enrich_word_articulation(w, speech_rate=speech_rate)
    return sentences


def word_alignment_report(sentences: list[SemanticSentence]) -> dict[str, Any]:
    words = [w for s in sentences for w in s.words]
    with_phone = sum(1 for w in words if w.phonemes)
    with_vis = sum(1 for w in words if w.visemes)
    return {
        "word_count": len(words),
        "with_phonemes": with_phone,
        "with_visemes": with_vis,
        "coverage": round(with_phone / max(1, len(words)), 3),
    }
