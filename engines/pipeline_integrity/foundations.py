"""Master Spec Part 1 Foundations — principles, invariants, owners, layers.

Authoritative runtime registry. Documentation: docs/FOUNDATIONS_PART1.md
ADR: docs/adr/ADR-012-foundations-part1.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

# ── Principles (Principle 1–9) ──────────────────────────────────────────────

PRINCIPLES: Final[tuple[str, ...]] = (
    "meaning_first",
    "sentence_first",
    "audio_first",
    "single_responsibility",
    "single_owner",
    "immutable_contracts",
    "deterministic_pipeline",
    "explainability",
    "architecture_before_ai",
)

# ── Main pipeline (no bypass) ───────────────────────────────────────────────

MAIN_PIPELINE: Final[tuple[str, ...]] = (
    "Video",
    "ASR",
    "Words",
    "Sentences",
    "Meaning",
    "Translation",
    "Validation",
    "Semantic Lock",
    "Planning",
    "Dub",
    "Scheduler",
    "Alignment",
    "Merge",
    "Studio",
    "Export",
)

# ── Layers ──────────────────────────────────────────────────────────────────

ARCHITECTURE_LAYERS: Final[dict[str, tuple[str, ...]]] = {
    "input": ("Video", "Audio", "Subtitles"),
    "recognition": ("Whisper", "ASR", "Words"),
    "semantic": ("Sentence Builder", "Context", "Meaning", "Dialogue", "Entities"),
    "translation": ("Translation", "Validation", "Semantic Lock"),
    "planning": ("Decision", "Planning", "Cost", "Confidence"),
    "dub": ("Speech", "Timing", "Scheduler", "Alignment"),
    "output": ("Merge", "Studio", "Export"),
}

# ── Absolute invariants (Invariant 1–8) ─────────────────────────────────────

INVARIANTS: Final[dict[str, str]] = {
    "I1_whisper_not_pipeline_owner": "Whisper owns recognition only — never the Pipeline",
    "I2_translation_ignorant_of_scheduler": "Translation never knows Scheduler",
    "I3_scheduler_ignorant_of_translation": "Scheduler never knows Translation",
    "I4_dub_ignorant_of_llm": "Dub Engine never knows LLM",
    "I5_tts_never_mutates_text": "TTS never changes text",
    "I6_merge_never_mutates_text": "Merge never changes text",
    "I7_studio_never_mutates_pipeline": "Studio never changes Pipeline",
    "I8_no_foreign_object_mutation": "No module may mutate foreign objects",
    "I9_no_segment_rule": "P116: No Whisper Segment / Chunk / Buffer / Window in semantic pipeline",
}

# ── Single Owner registry (Spec Part 1) ─────────────────────────────────────

SINGLE_OWNERS: Final[dict[str, str]] = {
    "Words": "Recognition",
    "Sentence": "Semantic Layer",
    "Translation": "Translation Engine",
    "Timing": "Scheduler",
    "Speech": "Dub Engine",
    "Audio": "TTS Engine",
    "Merge": "Merge Engine",
    "Export": "Studio",
}


@dataclass(frozen=True)
class EntityContract:
    name: str
    layer: str
    owner: str
    may_mutate: tuple[str, ...]
    must_not_mutate: tuple[str, ...]


ENTITIES: Final[tuple[EntityContract, ...]] = (
    EntityContract(
        "Word",
        "recognition",
        "Recognition",
        ("text", "start_ms", "end_ms", "confidence", "phonemes", "visemes"),
        ("translated_text", "timeline"),
    ),
    EntityContract(
        "SemanticSentence",
        "semantic",
        "Semantic Layer",
        ("text", "meaning", "entities", "context"),
        ("audio_file", "timeline_slots"),
    ),
    EntityContract(
        "SpeechUnit",
        "dub",
        "Dub Engine",
        ("speech_text", "expected_duration_ms", "emotion"),
        ("source_asr_segments",),
    ),
    EntityContract(
        "AudioUnit",
        "dub",
        "Scheduler",
        ("start_ms", "end_ms", "tempo", "file"),
        ("translated_text", "source_text"),
    ),
    EntityContract(
        "Timeline",
        "dub",
        "Scheduler",
        ("units", "slots"),
        ("text", "meaning"),
    ),
    EntityContract(
        "MeaningUnit",
        "semantic",
        "Semantic Layer",
        ("text", "sentences", "translated_text", "start_ms", "end_ms", "context"),
        ("audio_file", "timeline_slots", "tempo"),
    ),
)

# Forbidden cross-layer imports (architecture tests)
FORBIDDEN_IMPORT_EDGES: Final[tuple[tuple[str, str], ...]] = (
    ("engines.scheduler", "engines.translation_pipeline"),
    ("engines.scheduler", "engines.translation"),
    ("engines.dubbing_engine", "engines.llm"),
    ("engines.tts", "engines.translation_pipeline"),
)


def owner_of_entity(entity: str) -> str | None:
    return SINGLE_OWNERS.get(entity)


def assert_invariant_catalog_complete() -> None:
    assert len(INVARIANTS) == 9
    assert len(PRINCIPLES) == 9
    assert len(SINGLE_OWNERS) == 8


def foundations_report() -> dict[str, Any]:
    return {
        "spec": "Master Technical Specification Part 1 Foundations",
        "version": "6.0",
        "principles": list(PRINCIPLES),
        "invariants": dict(INVARIANTS),
        "owners": dict(SINGLE_OWNERS),
        "pipeline": list(MAIN_PIPELINE),
        "layers": {k: list(v) for k, v in ARCHITECTURE_LAYERS.items()},
        "entities": [
            {
                "name": e.name,
                "layer": e.layer,
                "owner": e.owner,
                "may_mutate": list(e.may_mutate),
                "must_not_mutate": list(e.must_not_mutate),
            }
            for e in ENTITIES
        ],
    }
