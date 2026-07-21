"""P18 Overflow / P19 Underflow / P20 Absolute / P21 No double audio."""

from __future__ import annotations

from engines.pipeline_integrity.exceptions import ArchitectureViolation
from engines.semantic_v3.types import SemanticSentence

OVERFLOW_ORDER = (
    "trim",
    "tempo",
    "stretch",
    "borrow",
    "sentence_merge",
    "semantic_rewrite",
    "manual_review",
)

UNDERFLOW_ORDER = (
    "tempo_down",
    "natural_pause",
    "breath",
    "silence_padding",
)


def apply_overflow_state(sent: SemanticSentence) -> SemanticSentence:
    if sent.overflow_ms > 0:
        sent.recovery_plan = list(OVERFLOW_ORDER)
    return sent


def apply_underflow_state(sent: SemanticSentence) -> SemanticSentence:
    if sent.underflow_ms > 0:
        # Never stretch words
        sent.recovery_plan = list(UNDERFLOW_ORDER)
    return sent


def assert_no_sentence_split_across_segments(
    sentences: list[SemanticSentence],
) -> None:
    """P20 — sentence is indivisible; each has exactly one dub_segment_uuid."""
    for s in sentences:
        if not s.dub_segment_uuid:
            raise ArchitectureViolation(
                "P20: sentence missing dub_segment_uuid",
                stage="absolute_rules",
                rule="sentence_indivisible",
                segment_id=s.sentence_uuid,
            )


def assert_no_tail_spill(
    sentences: list[SemanticSentence],
) -> None:
    """P21 — forbid carrying sentence tail into the next segment's audio."""
    # Structural check: no sentence may claim words belonging to another span
    for i, s in enumerate(sentences):
        if not s.words:
            continue
        for w in s.words:
            if w.start_ms < s.start_ms - 5 or w.end_ms > s.end_ms + 5:
                raise ArchitectureViolation(
                    "P21: word timing outside sentence span (tail spill risk)",
                    stage="absolute_rules",
                    rule="no_double_audio",
                    segment_id=s.sentence_uuid,
                    details={"word": w.text, "index": i},
                )


def assert_no_overlap_slots(sentences: list[SemanticSentence]) -> None:
    """No overlapping sentence time windows (audio overlap root)."""
    ordered = sorted(sentences, key=lambda s: s.start_ms)
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if a.end_ms > b.start_ms + 40:
            raise ArchitectureViolation(
                f"P21: sentence time overlap {a.end_ms - b.start_ms}ms",
                stage="absolute_rules",
                rule="no_audio_overlap",
                segment_id=a.sentence_uuid,
            )
