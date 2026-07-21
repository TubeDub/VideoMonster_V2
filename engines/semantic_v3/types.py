"""Core Semantic V3 types — P0/P1/P2."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


def _new_uuid() -> str:
    return uuid.uuid4().hex


@dataclass
class SemanticWord:
    """P1/P35/P101 — first-class word object (Whisper is only a timestamp source)."""

    text: str
    start_ms: int
    end_ms: int
    word_uuid: str = field(default_factory=_new_uuid)
    duration_ms: int = 0
    speaker: str = ""
    confidence: float = 1.0
    phonemes: list[str] = field(default_factory=list)
    visemes: list[str] = field(default_factory=list)
    stress: float = 0.0
    syllables: int = 0
    importance: float = 0.5
    entity: str = ""  # PERSON|ORG|PLACE|BRAND|NUMBER|DATE|OTHER|""
    pause_before_ms: int = 0
    pause_after_ms: int = 0
    breath_before: bool = False
    breath_after: bool = False
    # P35 / P101 extended fields
    lemma: str = ""
    language: str = ""
    dependency: str = ""
    sentence_uuid: str = ""
    speaker_uuid: str = ""
    normalized_text: str = ""
    paragraph_uuid: str = ""
    scene_uuid: str = ""
    entity_type: str = ""
    entity_id: str = ""
    importance_score: float = 0.5
    prosody: str = ""
    emotion_hint: str = ""
    dependency_parent: str = ""
    dependency_children: list[str] = field(default_factory=list)
    # P101 additional fields
    punctuation: str = ""
    sentence_candidate: bool = False
    semantic_group: str = ""

    def __post_init__(self) -> None:
        if self.duration_ms <= 0:
            self.duration_ms = max(0, int(self.end_ms) - int(self.start_ms))
        if self.syllables <= 0 and self.text:
            self.syllables = max(1, sum(1 for c in self.text.lower() if c in "aeiouyаеєиіїоуюяё"))
        if not self.normalized_text and self.text:
            self.normalized_text = self.text.strip(".,!?;:\"'«»").lower()
        if not self.entity_type and self.entity:
            self.entity_type = self.entity
        if self.importance_score == 0.5 and self.importance != 0.5:
            self.importance_score = self.importance

    @property
    def pause_before(self) -> int:
        return self.pause_before_ms

    @property
    def pause_after(self) -> int:
        return self.pause_after_ms

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pause_before"] = self.pause_before_ms
        d["pause_after"] = self.pause_after_ms
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SemanticWord":
        return cls(
            text=str(data.get("text") or ""),
            start_ms=int(data.get("start_ms") or 0),
            end_ms=int(data.get("end_ms") or 0),
            word_uuid=str(data.get("word_uuid") or _new_uuid()),
            duration_ms=int(data.get("duration_ms") or 0),
            speaker=str(data.get("speaker") or ""),
            confidence=float(data.get("confidence") or 1.0),
            phonemes=list(data.get("phonemes") or []),
            visemes=list(data.get("visemes") or []),
            stress=float(data.get("stress") or 0.0),
            syllables=int(data.get("syllables") or 0),
            importance=float(data.get("importance") or 0.5),
            entity=str(data.get("entity") or ""),
            pause_before_ms=int(
                data.get("pause_before_ms") or data.get("pause_before") or 0
            ),
            pause_after_ms=int(
                data.get("pause_after_ms") or data.get("pause_after") or 0
            ),
            breath_before=bool(data.get("breath_before")),
            breath_after=bool(data.get("breath_after")),
            lemma=str(data.get("lemma") or ""),
            language=str(data.get("language") or ""),
            dependency=str(data.get("dependency") or ""),
            sentence_uuid=str(data.get("sentence_uuid") or ""),
            speaker_uuid=str(data.get("speaker_uuid") or ""),
            normalized_text=str(data.get("normalized_text") or ""),
            paragraph_uuid=str(data.get("paragraph_uuid") or ""),
            scene_uuid=str(data.get("scene_uuid") or ""),
            entity_type=str(data.get("entity_type") or data.get("entity") or ""),
            entity_id=str(data.get("entity_id") or ""),
            importance_score=float(
                data.get("importance_score") or data.get("importance") or 0.5
            ),
            prosody=str(data.get("prosody") or ""),
            emotion_hint=str(data.get("emotion_hint") or ""),
            dependency_parent=str(data.get("dependency_parent") or ""),
            dependency_children=list(data.get("dependency_children") or []),
            punctuation=str(data.get("punctuation") or ""),
            sentence_candidate=bool(data.get("sentence_candidate")),
            semantic_group=str(data.get("semantic_group") or ""),
        )


@dataclass
class SemanticSentence:
    """P2 — indivisible meaning unit (Absolute Rule P20)."""

    sentence_uuid: str = field(default_factory=_new_uuid)
    text: str = ""
    translated_text: str = ""
    words: list[SemanticWord] = field(default_factory=list)
    start_ms: int = 0
    end_ms: int = 0
    speaker: str = ""
    # Structural tags (P2)
    is_direct_speech: bool = False
    is_enumeration: bool = False
    is_subordinate: bool = False
    is_complex: bool = False
    is_dialogue: bool = False
    has_address: bool = False
    has_parenthetical: bool = False
    emotion: str = "neutral"
    # P3 semantic graph
    entities: list[str] = field(default_factory=list)
    verbs: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    intent: str = ""
    meaning_vector: list[float] = field(default_factory=list)
    context_links: list[str] = field(default_factory=list)  # neighbor sentence_uuids
    # P7 Semantic Lock
    semantic_locked: bool = False
    locked_entities: list[str] = field(default_factory=list)
    locked_numbers: list[str] = field(default_factory=list)
    meaning_fingerprint: str = ""
    # P8–P9 timing / predictor
    ideal_duration_ms: int = 0
    predicted_tts_ms: int = 0
    speech_rate: float = 1.0
    # P13 dynamic segment (created AFTER translation)
    dub_segment_uuid: str = ""
    # Scores (P22)
    meaning_score: float = 100.0
    entity_score: float = 100.0
    timing_score: float = 100.0
    # Overflow/underflow state (P18/P19)
    overflow_ms: int = 0
    underflow_ms: int = 0
    recovery_plan: list[str] = field(default_factory=list)
    # P105 Semantic Core sentence object
    scene_uuid: str = ""
    dialogue_id: str = ""
    style: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    sentence_confidence: float = 1.0
    estimated_duration: int = 0
    translation_status: str = "pending"  # pending|ready|translated|locked
    semantic_status: str = "raw"  # raw|analyzed|validated|needs_review
    lock_status: str = "unlocked"  # unlocked|prepared|locked
    parent_topic: str = ""
    child_thoughts: list[str] = field(default_factory=list)
    sentence_type: str = "simple"  # simple|complex|compound|subordinate|enumeration|...
    is_question: bool = False
    is_exclamation: bool = False
    is_incomplete: bool = False

    @property
    def slot_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["words"] = [w.to_dict() if isinstance(w, SemanticWord) else w for w in self.words]
        d["slot_ms"] = self.slot_ms
        d["confidence"] = self.sentence_confidence
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SemanticSentence":
        words = [
            SemanticWord.from_dict(w) if isinstance(w, dict) else w
            for w in (data.get("words") or [])
        ]
        return cls(
            sentence_uuid=str(data.get("sentence_uuid") or _new_uuid()),
            text=str(data.get("text") or ""),
            translated_text=str(data.get("translated_text") or ""),
            words=words,
            start_ms=int(data.get("start_ms") or 0),
            end_ms=int(data.get("end_ms") or 0),
            speaker=str(data.get("speaker") or ""),
            is_direct_speech=bool(data.get("is_direct_speech")),
            is_enumeration=bool(data.get("is_enumeration")),
            is_subordinate=bool(data.get("is_subordinate")),
            is_complex=bool(data.get("is_complex")),
            is_dialogue=bool(data.get("is_dialogue")),
            has_address=bool(data.get("has_address")),
            has_parenthetical=bool(data.get("has_parenthetical")),
            emotion=str(data.get("emotion") or "neutral"),
            entities=list(data.get("entities") or []),
            verbs=list(data.get("verbs") or []),
            subjects=list(data.get("subjects") or []),
            objects=list(data.get("objects") or []),
            relations=list(data.get("relations") or []),
            intent=str(data.get("intent") or ""),
            meaning_vector=list(data.get("meaning_vector") or []),
            context_links=list(data.get("context_links") or []),
            semantic_locked=bool(data.get("semantic_locked")),
            locked_entities=list(data.get("locked_entities") or []),
            locked_numbers=list(data.get("locked_numbers") or []),
            meaning_fingerprint=str(data.get("meaning_fingerprint") or ""),
            ideal_duration_ms=int(data.get("ideal_duration_ms") or 0),
            predicted_tts_ms=int(data.get("predicted_tts_ms") or 0),
            speech_rate=float(data.get("speech_rate") or 1.0),
            dub_segment_uuid=str(data.get("dub_segment_uuid") or ""),
            meaning_score=float(data.get("meaning_score") or 100.0),
            entity_score=float(data.get("entity_score") or 100.0),
            timing_score=float(data.get("timing_score") or 100.0),
            overflow_ms=int(data.get("overflow_ms") or 0),
            underflow_ms=int(data.get("underflow_ms") or 0),
            recovery_plan=list(data.get("recovery_plan") or []),
            scene_uuid=str(data.get("scene_uuid") or ""),
            dialogue_id=str(data.get("dialogue_id") or ""),
            style=str(data.get("style") or ""),
            context=dict(data.get("context") or {}),
            sentence_confidence=float(
                data.get("sentence_confidence") or data.get("confidence") or 1.0
            ),
            estimated_duration=int(data.get("estimated_duration") or 0),
            translation_status=str(data.get("translation_status") or "pending"),
            semantic_status=str(data.get("semantic_status") or "raw"),
            lock_status=str(data.get("lock_status") or "unlocked"),
            parent_topic=str(data.get("parent_topic") or ""),
            child_thoughts=list(data.get("child_thoughts") or []),
            sentence_type=str(data.get("sentence_type") or "simple"),
            is_question=bool(data.get("is_question")),
            is_exclamation=bool(data.get("is_exclamation")),
            is_incomplete=bool(data.get("is_incomplete")),
        )


@dataclass
class MeaningUnit:
    """P103 — completed thought unit. The primary pipeline unit after sentence reconstruction.

    A MeaningUnit can contain:
    - one sentence (most common case)
    - multiple sentences (when they form a single thought)
    - part of a long sentence (when a sentence contains multiple independent thoughts)

    Criterion: a completed thought (законченная мысль).
    """

    unit_uuid: str = field(default_factory=_new_uuid)
    sentences: list[SemanticSentence] = field(default_factory=list)
    text: str = ""
    translated_text: str = ""
    start_ms: int = 0
    end_ms: int = 0
    speaker: str = ""

    # P104 Context Graph
    prev_unit_uuid: str = ""
    next_unit_uuid: str = ""
    active_characters: list[str] = field(default_factory=list)
    place: str = ""
    emotion: str = "neutral"
    speech_style: str = ""  # formal|informal|narrative|dialogue|monologue
    topic: str = ""
    terminology: list[str] = field(default_factory=list)
    dialogue_history: list[str] = field(default_factory=list)

    # P106 Semantic Adaptation variants
    adaptation_variants: list[dict[str, Any]] = field(default_factory=list)
    selected_variant_id: str = ""

    # P107 Duration Prediction
    predicted_duration_ms: int = 0
    prediction_confidence: float = 0.0

    # P108 Strategy Scores
    meaning_score: float = 100.0
    naturalness_score: float = 100.0
    dialogue_score: float = 100.0
    duration_score: float = 100.0
    emotion_score: float = 100.0
    prosody_score: float = 100.0
    lipsync_readiness: float = 100.0
    runtime_cost: float = 0.0

    # P109 Translation Lock
    semantic_locked: bool = False
    lock_status: str = "unlocked"  # unlocked|prepared|locked

    # P110 Speech Planning
    expected_tempo: float = 1.0
    expected_pauses: list[int] = field(default_factory=list)
    expected_breaths: list[int] = field(default_factory=list)
    overflow_probability: float = 0.0
    underflow_probability: float = 0.0

    # P119 Validation
    validation_status: str = "pending"  # pending|passed|failed
    validation_errors: list[str] = field(default_factory=list)

    # Metadata
    meaning_complete: bool = True
    sentence_count: int = 0
    word_count: int = 0

    @property
    def slot_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def words(self) -> list[SemanticWord]:
        result = []
        for s in self.sentences:
            result.extend(s.words)
        return result

    def __post_init__(self) -> None:
        if not self.text and self.sentences:
            self.text = " ".join(s.text for s in self.sentences if s.text)
        if not self.start_ms and self.sentences:
            self.start_ms = self.sentences[0].start_ms
        if not self.end_ms and self.sentences:
            self.end_ms = self.sentences[-1].end_ms
        if not self.speaker and self.sentences:
            self.speaker = self.sentences[0].speaker
        self.sentence_count = len(self.sentences)
        self.word_count = sum(len(s.words) for s in self.sentences)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sentences"] = [s.to_dict() if isinstance(s, SemanticSentence) else s for s in self.sentences]
        d["slot_ms"] = self.slot_ms
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MeaningUnit":
        sentences = [
            SemanticSentence.from_dict(s) if isinstance(s, dict) else s
            for s in (data.get("sentences") or [])
        ]
        return cls(
            unit_uuid=str(data.get("unit_uuid") or _new_uuid()),
            sentences=sentences,
            text=str(data.get("text") or ""),
            translated_text=str(data.get("translated_text") or ""),
            start_ms=int(data.get("start_ms") or 0),
            end_ms=int(data.get("end_ms") or 0),
            speaker=str(data.get("speaker") or ""),
            prev_unit_uuid=str(data.get("prev_unit_uuid") or ""),
            next_unit_uuid=str(data.get("next_unit_uuid") or ""),
            active_characters=list(data.get("active_characters") or []),
            place=str(data.get("place") or ""),
            emotion=str(data.get("emotion") or "neutral"),
            speech_style=str(data.get("speech_style") or ""),
            topic=str(data.get("topic") or ""),
            terminology=list(data.get("terminology") or []),
            dialogue_history=list(data.get("dialogue_history") or []),
            adaptation_variants=list(data.get("adaptation_variants") or []),
            selected_variant_id=str(data.get("selected_variant_id") or ""),
            predicted_duration_ms=int(data.get("predicted_duration_ms") or 0),
            prediction_confidence=float(data.get("prediction_confidence") or 0.0),
            meaning_score=float(data.get("meaning_score") or 100.0),
            naturalness_score=float(data.get("naturalness_score") or 100.0),
            dialogue_score=float(data.get("dialogue_score") or 100.0),
            duration_score=float(data.get("duration_score") or 100.0),
            emotion_score=float(data.get("emotion_score") or 100.0),
            prosody_score=float(data.get("prosody_score") or 100.0),
            lipsync_readiness=float(data.get("lipsync_readiness") or 100.0),
            runtime_cost=float(data.get("runtime_cost") or 0.0),
            semantic_locked=bool(data.get("semantic_locked")),
            lock_status=str(data.get("lock_status") or "unlocked"),
            expected_tempo=float(data.get("expected_tempo") or 1.0),
            expected_pauses=list(data.get("expected_pauses") or []),
            expected_breaths=list(data.get("expected_breaths") or []),
            overflow_probability=float(data.get("overflow_probability") or 0.0),
            underflow_probability=float(data.get("underflow_probability") or 0.0),
            validation_status=str(data.get("validation_status") or "pending"),
            validation_errors=list(data.get("validation_errors") or []),
            meaning_complete=bool(data.get("meaning_complete", True)),
        )


@dataclass
class SemanticProject:
    """Project-level Semantic V3 state (Whisper segments archived, not authoritative)."""

    project_uuid: str = field(default_factory=_new_uuid)
    words: list[SemanticWord] = field(default_factory=list)
    sentences: list[SemanticSentence] = field(default_factory=list)
    meaning_units: list[MeaningUnit] = field(default_factory=list)
    asr_archive: list[dict[str, Any]] = field(default_factory=list)  # Whisper segs (non-owner)
    unit_type: str = "semantic_sentence"
    phase: str = "P0"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_uuid": self.project_uuid,
            "unit_type": self.unit_type,
            "phase": self.phase,
            "words": [w.to_dict() for w in self.words],
            "sentences": [s.to_dict() for s in self.sentences],
            "meaning_units": [mu.to_dict() for mu in self.meaning_units],
            "asr_archive": list(self.asr_archive),
            "meta": dict(self.meta),
        }

    def to_pipeline_arrays(self) -> tuple[list[str], list[dict[str, int]], list[dict[str, Any]]]:
        """DEPRECATED (P31): Phase-1 bridge helper. Prefer phase2_to_orchestrator_arrays."""
        source_segments = [s.text for s in self.sentences]
        timing_map = [{"start": s.start_ms, "end": s.end_ms} for s in self.sentences]
        sentence_rows = [s.to_dict() for s in self.sentences]
        return source_segments, timing_map, sentence_rows
