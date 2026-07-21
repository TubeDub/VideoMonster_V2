"""Semantic V3 pipeline orchestrator — P0 destroy Whisper ownership → sentences."""

from __future__ import annotations

import logging
from typing import Any

from engines.semantic_v3.absolute_rules import (
    apply_overflow_state,
    apply_underflow_state,
    assert_no_overlap_slots,
    assert_no_sentence_split_across_segments,
    assert_no_tail_spill,
)
from engines.semantic_v3.adaptation import run_adaptation_ladder
from engines.semantic_v3.quality import review_payload, validate_all
from engines.semantic_v3.semantic_graph import analyze_all
from engines.semantic_v3.semantic_lock import lock_all
from engines.semantic_v3.sentence_builder import (
    assert_sentences_atomic,
    build_sentences_from_words,
)
from engines.semantic_v3.timing_predictor import apply_audio_predictor
from engines.semantic_v3.types import SemanticProject
from engines.semantic_v3.word_engine import build_words_from_timing_map

logger = logging.getLogger("tubedub.semantic_v3")


def run_semantic_v3_from_asr(
    source_segments: list[str],
    timing_map: list[Any],
    *,
    word_maps: list[Any] | None = None,
    tgt_lang: str = "uk",
    translations: list[str] | None = None,
    lock: bool = False,
    run_adaptation: bool = True,
) -> SemanticProject:
    """
    P0: archive Whisper segments; build words → sentences as sole units.

    ``source_segments`` / ``timing_map`` here are treated as *ASR archive input*,
    not as durable dub identity.
    """
    archive = []
    for i, text in enumerate(source_segments):
        row: dict[str, Any] = {"index": i, "text": text}
        if i < len(timing_map):
            tm = timing_map[i]
            if isinstance(tm, dict):
                row["start"] = tm.get("start")
                row["end"] = tm.get("end")
            elif isinstance(tm, (list, tuple)) and len(tm) >= 2:
                row["start"], row["end"] = tm[0], tm[1]
        archive.append(row)

    words = build_words_from_timing_map(source_segments, timing_map, word_maps)
    sentences = build_sentences_from_words(words)
    assert_sentences_atomic(sentences)
    sentences = analyze_all(sentences)

    if translations:
        for i, s in enumerate(sentences):
            if i < len(translations) and translations[i]:
                s.translated_text = str(translations[i]).strip()

    sentences = apply_audio_predictor(sentences, tgt_lang=tgt_lang)
    for s in sentences:
        apply_overflow_state(s)
        apply_underflow_state(s)

    if run_adaptation:
        sentences = run_adaptation_ladder(sentences, allow_rewrite=True)
    else:
        from engines.semantic_v3.adaptation import assign_dub_segments

        sentences = assign_dub_segments(sentences)

    if lock or translations:
        sentences = lock_all(sentences)

    assert_no_sentence_split_across_segments(sentences)
    assert_no_tail_spill(sentences)
    assert_no_overlap_slots(sentences)

    qa = validate_all(sentences)
    project = SemanticProject(
        words=words,
        sentences=sentences,
        asr_archive=archive,
        unit_type="semantic_sentence",
        phase="P13",
        meta={
            "whisper_segments_archived": len(archive),
            "word_count": len(words),
            "sentence_count": len(sentences),
            "validation": qa,
            "review": review_payload(sentences),
        },
    )
    logger.info(
        "SemanticV3: archived_whisper=%d words=%d sentences=%d validation_ok=%s",
        len(archive),
        len(words),
        len(sentences),
        qa.get("ok"),
    )
    return project
