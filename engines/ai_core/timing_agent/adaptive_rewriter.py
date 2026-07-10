"""Orchestrate rule + LLM shorten/expand candidates."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.timing_agent.llm_rewrite import llm_expand, llm_shorten
from engines.ai_core.timing_agent.rule_rewrite import (
    generate_expand_candidates,
    generate_shorten_candidates,
)
from engines.timing_fit import DUB_SLOT_TOLERANCE_MS

_DIRECTION_OVERFLOW = "overflow"
_DIRECTION_UNDERFLOW = "underflow"
_DIRECTION_FIT = "fit"


@dataclass
class AdaptDirection:
    direction: str  # overflow|underflow|fit
    slot_ms: int
    predicted_ms: int
    delta_ms: int


@dataclass
class CandidateBatch:
    variants: dict[str, str]
    llm_used: bool = False
    rule_rewrite_used: bool = True
    direction: str = _DIRECTION_FIT
    decision_log: list[str] = field(default_factory=list)


def classify_slot_delta(predicted_ms: int, slot_ms: int) -> AdaptDirection:
    tolerance = DUB_SLOT_TOLERANCE_MS
    delta = predicted_ms - slot_ms
    if delta > tolerance:
        return AdaptDirection(_DIRECTION_OVERFLOW, slot_ms, predicted_ms, delta)
    if predicted_ms < int(slot_ms * 0.82):
        return AdaptDirection(_DIRECTION_UNDERFLOW, slot_ms, predicted_ms, delta)
    return AdaptDirection(_DIRECTION_FIT, slot_ms, predicted_ms, delta)


def generate_adaptive_candidates(
    text: str,
    *,
    slot_ms: int,
    predicted_ms: int,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    use_llm: bool = True,
) -> CandidateBatch:
    """Generate 3 candidates A/B/C for overflow or underflow; fit returns original only."""
    direction = classify_slot_delta(predicted_ms, slot_ms)
    base = str(text or "").strip()

    if direction.direction == _DIRECTION_FIT:
        return CandidateBatch(
            variants={"ORIGINAL": base},
            llm_used=False,
            rule_rewrite_used=False,
            direction=_DIRECTION_FIT,
            decision_log=["already_fits_slot"],
        )

    if direction.direction == _DIRECTION_OVERFLOW:
        variants = generate_shorten_candidates(
            base, tgt_lang=tgt_lang, prev_context=prev_context
        )
        llm_used = False
        if use_llm:
            llm_text, llm_ok = llm_shorten(
                base,
                slot_ms=slot_ms,
                tgt_lang=tgt_lang,
                prev_context=prev_context,
            )
            if llm_text and llm_text.strip():
                variants["LLM"] = llm_text.strip()
                llm_used = llm_ok
        return CandidateBatch(
            variants=variants,
            llm_used=llm_used,
            rule_rewrite_used=True,
            direction=_DIRECTION_OVERFLOW,
            decision_log=[f"overflow delta_ms={direction.delta_ms}"],
        )

    variants = generate_expand_candidates(base, tgt_lang=tgt_lang)
    llm_used = False
    if use_llm:
        llm_text, llm_ok = llm_expand(base, slot_ms=slot_ms, tgt_lang=tgt_lang)
        if llm_text and llm_text.strip():
            variants["LLM"] = llm_text.strip()
            llm_used = llm_ok
    return CandidateBatch(
        variants=variants,
        llm_used=llm_used,
        rule_rewrite_used=True,
        direction=_DIRECTION_UNDERFLOW,
        decision_log=[f"underflow delta_ms={direction.delta_ms}"],
    )


@dataclass
class AdaptResult:
    text: str
    rule_rewrite_used: bool = False
    llm_rewrite_used: bool = False


def build_candidate_variants(
    text: str,
    *,
    slot_ms: int,
    predicted_ms: int,
    tgt_lang: str = "ru",
    use_llm: bool = True,
    prev_context: str | None = None,
) -> tuple[dict[str, str], bool, bool]:
    """Build variant dict plus rule/llm flags (test helper)."""
    batch = generate_adaptive_candidates(
        text,
        slot_ms=slot_ms,
        predicted_ms=predicted_ms,
        tgt_lang=tgt_lang,
        prev_context=prev_context,
        use_llm=use_llm,
    )
    return batch.variants, batch.rule_rewrite_used, batch.llm_used


def adapt_for_overflow(
    text: str,
    *,
    slot_ms: int,
    predicted_ms: int,
    tgt_lang: str = "ru",
    use_llm: bool = True,
    attempt: int = 0,
) -> AdaptResult:
    labels = ("A", "B", "C")
    label = labels[attempt % len(labels)]
    variants = generate_shorten_candidates(text, tgt_lang=tgt_lang)
    out = variants.get(label) or text
    llm_used = False
    if use_llm and attempt >= 1:
        llm_text, llm_ok = llm_shorten(text, slot_ms=slot_ms, tgt_lang=tgt_lang)
        if llm_text:
            out = llm_text
            llm_used = llm_ok
    return AdaptResult(text=out, rule_rewrite_used=True, llm_rewrite_used=llm_used)


def adapt_for_underflow(
    text: str,
    *,
    slot_ms: int,
    predicted_ms: int,
    tgt_lang: str = "ru",
    use_llm: bool = True,
    attempt: int = 0,
) -> AdaptResult:
    labels = ("A", "B", "C")
    label = labels[attempt % len(labels)]
    variants = generate_expand_candidates(text, tgt_lang=tgt_lang)
    out = variants.get(label) or text
    llm_used = False
    if use_llm and attempt >= 1:
        llm_text, llm_ok = llm_expand(text, slot_ms=slot_ms, tgt_lang=tgt_lang)
        if llm_text:
            out = llm_text
            llm_used = llm_ok
    return AdaptResult(text=out, rule_rewrite_used=True, llm_rewrite_used=llm_used)
