"""Master Spec Part 2 — Semantic Core orchestrator (P101–P120).

Audio → Whisper → Word Timeline → Word Graph → Sentence Builder →
Boundary Optimizer → Semantic Graph → Dialogue Graph → Scene Context →
Conversation Memory → (ready for Translation)

Whisper is not a decision maker after this stage.
"""

from __future__ import annotations

import logging
from typing import Any

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.boundary_optimizer import optimize_boundaries
from engines.semantic_v3.context_memory import build_context_memory
from engines.semantic_v3.conversation_memory import (
    ConversationMemory,
    build_conversation_memory,
)
from engines.semantic_v3.dialogue_engine import build_dialogues
from engines.semantic_v3.emotion_engine import apply_emotion_engine
from engines.semantic_v3.entity_graph import build_entity_graph
from engines.semantic_v3.lock_preparation import prepare_semantic_lock
from engines.semantic_v3.scene_context import assign_scenes
from engines.semantic_v3.semantic_graph import analyze_all
from engines.semantic_v3.semantic_validator import validate_semantic_sentences
from engines.semantic_v3.sentence_builder import assert_sentences_atomic
from engines.semantic_v3.sentence_confidence import apply_sentence_confidence
from engines.semantic_v3.style_engine import apply_style_engine
from engines.semantic_v3.types import SemanticProject, SemanticSentence
from engines.semantic_v3.word_engine import build_words_from_timing_map
from engines.semantic_v3.word_graph import build_word_graph
from engines.semantic_v3.word_model import assert_word_model_complete, enrich_word_model

logger = logging.getLogger("tubedub.semantic_v3.semantic_core")

FORBIDDEN_UNIT_TYPES = frozenset(
    {"whisper_segment", "chunk", "buffer", "window", "segment"}
)


def assert_semantic_sentence_only(unit: Any) -> None:
    """P117 — only SemanticSentence is a valid meaning unit."""
    if isinstance(unit, SemanticSentence):
        return
    ut = getattr(unit, "unit_type", None) or (
        unit.get("unit_type") if isinstance(unit, dict) else None
    )
    if ut in FORBIDDEN_UNIT_TYPES:
        raise ArchitectureViolation(
            f"P117: forbidden meaning unit {ut!r}",
            stage="semantic_core",
            rule="sentence_only",
        )
    if not isinstance(unit, SemanticSentence):
        raise ArchitectureViolation(
            "P117: only SemanticSentence allowed",
            stage="semantic_core",
            rule="sentence_only",
        )


def run_semantic_core(
    asr_texts: list[str],
    asr_timing: list[Any],
    *,
    word_maps: list[Any] | None = None,
    src_lang: str = "en",
    content_mode: str = "",
    style_hint: str = "",
    project_uuid: str = "",
    place: str = "",
    terminology: list[str] | None = None,
    reanalyze_passes: int = 1,
) -> SemanticProject:
    """
    Build full Semantic Core state before Translation.
    Does not call Translation Engine / Semantic Lock.
    """
    archive = []
    for i, text in enumerate(asr_texts):
        row: dict[str, Any] = {
            "index": i,
            "text": text,
            "unit_type": "asr_archive_only",
        }
        if i < len(asr_timing) and isinstance(asr_timing[i], dict):
            row["start"] = asr_timing[i].get("start")
            row["end"] = asr_timing[i].get("end")
        archive.append(row)

    # P101 words
    words = build_words_from_timing_map(asr_texts, asr_timing, word_maps)
    words = enrich_word_model(words, language=src_lang)
    assert_word_model_complete(words)

    # P103/P104 sentences
    sentences = optimize_boundaries(words)
    assert_sentences_atomic(sentences)
    for s in sentences:
        assert_semantic_sentence_only(s)

    # P105/P106 confidence + typing
    sentences = apply_sentence_confidence(sentences)

    # Optional re-pass for low confidence (return to builder)
    for _ in range(max(0, reanalyze_passes)):
        weak = [s for s in sentences if s.semantic_status == "needs_review"]
        if not weak:
            break
        # Rebuild only from word lattice (deterministic) then re-score
        sentences = optimize_boundaries(
            [w for s in sentences for w in s.words] or words
        )
        sentences = apply_sentence_confidence(sentences)

    # P107 semantic graph + P114 neighbor links
    sentences = analyze_all(sentences)
    sentences = build_context_memory(sentences, place=place)

    # P102 word graph
    word_graph = build_word_graph(sentences)

    # P108 entities
    entity_graph = build_entity_graph(sentences)

    # P112 emotion before style/dialogue
    sentences = apply_emotion_engine(sentences)

    # P113 style
    style = apply_style_engine(
        sentences, content_mode=content_mode, hint=style_hint
    )

    # P109 dialogues
    dialogues = build_dialogues(sentences)

    # P111 scenes
    scenes = assign_scenes(sentences)

    # P110 conversation memory
    proj_id = project_uuid or (archive[0].get("text", "")[:8] if archive else "proj")
    memory = build_conversation_memory(
        sentences, project_uuid=str(proj_id), terminology=terminology
    )

    # Estimated duration from slot
    for s in sentences:
        s.estimated_duration = s.slot_ms
        s.translation_status = "ready"

    # P115 validator
    validation = validate_semantic_sentences(sentences)

    # P116 lock preparation (not lock)
    lock_prep = prepare_semantic_lock(sentences)

    project = SemanticProject(
        words=words,
        sentences=sentences,
        asr_archive=archive,
        unit_type="semantic_sentence",
        phase="P120",
        meta={
            "semantic_core": True,
            "whisper_owner": False,
            "bridge": False,
            "style": style,
            "word_graph": word_graph.to_dict(),
            "entity_graph": entity_graph.to_dict(),
            "dialogues": [d.to_dict() for d in dialogues],
            "scenes": [sc.to_dict() for sc in scenes],
            "conversation_memory": memory.to_dict(),
            "validation": validation.to_dict(),
            "lock_preparation": [p.to_dict() for p in lock_prep],
            "needs_review": sum(
                1 for s in sentences if s.semantic_status == "needs_review"
            ),
        },
    )
    if project_uuid:
        project.project_uuid = str(project_uuid)
    logger.info(
        "SemanticCore: archive=%d words=%d sentences=%d style=%s validation_ok=%s",
        len(archive),
        len(words),
        len(sentences),
        style,
        validation.ok,
    )
    return project


def clear_project_memory(project: SemanticProject) -> ConversationMemory | None:
    """P110 — clear conversation memory when project ends."""
    raw = (project.meta or {}).get("conversation_memory")
    if not raw:
        return None
    mem = ConversationMemory(project_uuid=str(raw.get("project_uuid") or ""))
    mem.names = list(raw.get("names") or [])
    mem.clear()
    project.meta["conversation_memory"] = mem.to_dict()
    return mem
