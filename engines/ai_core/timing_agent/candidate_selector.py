"""Generate 3 variants A/B/C and pick best slot fit."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.timing_agent.adaptive_rewriter import (
    CandidateBatch,
    classify_slot_delta,
    generate_adaptive_candidates,
)
from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
from engines.ai_core.timing_agent.scoring import CandidateTimingScore, score_candidate


@dataclass
class SelectionResult:
    best: CandidateTimingScore
    candidates: list[CandidateTimingScore]
    llm_rewrite_used: bool = False
    rule_rewrite_used: bool = False
    direction: str = "fit"
    decision_log: list[str] = field(default_factory=list)

    @property
    def llm_used(self) -> bool:
        return self.llm_rewrite_used


def needs_adaptation(predicted_ms: int, slot_ms: int) -> str:
    """Return adaptation direction: shorten | expand | none."""
    direction = classify_slot_delta(predicted_ms, slot_ms).direction
    if direction == "overflow":
        return "shorten"
    if direction == "underflow":
        return "expand"
    return "none"


def select_best_candidate(
    source: str,
    semantic_text: str,
    batch: CandidateBatch,
    *,
    slot_ms: int,
    tgt_lang: str = "ru",
) -> SelectionResult:
    scored: list[CandidateTimingScore] = []
    for label, text in batch.variants.items():
        if not str(text or "").strip():
            continue
        kind = "llm" if label == "LLM" else "rule"
        scored.append(
            score_candidate(
                text,
                source=source,
                semantic_text=semantic_text,
                slot_ms=slot_ms,
                tgt_lang=tgt_lang,
                variant=label,
                source_kind=kind,
            )
        )

    if not scored:
        fallback = score_candidate(
            semantic_text,
            source=source,
            semantic_text=semantic_text,
            slot_ms=slot_ms,
            tgt_lang=tgt_lang,
            variant="fallback",
        )
        return SelectionResult(
            best=fallback,
            candidates=[fallback],
            decision_log=["no_valid_candidates"],
        )

    best = max(scored, key=lambda c: c.scores.overall)
    log = batch.decision_log + [
        f"selected={best.variant} slot_fit={best.scores.slot_fit:.3f} "
        f"predicted={best.predicted_ms} slot={slot_ms}"
    ]
    return SelectionResult(
        best=best,
        candidates=scored,
        llm_rewrite_used=batch.llm_used,
        rule_rewrite_used=batch.rule_rewrite_used,
        direction=batch.direction,
        decision_log=log,
    )


def generate_and_select(
    semantic_text: str,
    *,
    source: str,
    slot_ms: int,
    tgt_lang: str = "ru",
    use_llm: bool = True,
) -> SelectionResult:
    """Predict duration, build candidates, pick best slot fit."""
    predicted = predict_duration_ms(semantic_text, tgt_lang)
    batch = generate_adaptive_candidates(
        semantic_text,
        slot_ms=slot_ms,
        predicted_ms=predicted,
        tgt_lang=tgt_lang,
        use_llm=use_llm,
    )
    return select_best_candidate(
        source,
        semantic_text,
        batch,
        slot_ms=slot_ms,
        tgt_lang=tgt_lang,
    )
