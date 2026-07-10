"""Retry policy — max 3 intelligent timing adaptation attempts."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.timing_agent.candidate_selector import generate_and_select, needs_adaptation
from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
from engines.ai_core.timing_agent.validators.sentence_integrity import validate_sentence_integrity
from engines.ai_core.timing_agent.validators.slot_fit_validator import validate_slot_fit

MAX_ATTEMPTS = 3
SLOT_FIT_THRESHOLD = 0.85


@dataclass
class RetryResult:
    text: str
    predicted_ms: int
    slot_fit_score: float
    attempts: int
    selected_variant: str
    rule_rewrite_used: bool = False
    llm_rewrite_used: bool = False
    micro_stretch_recommended: bool = False
    used_fallback: bool = False
    decision_log: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)


def apply_retry_policy(
    semantic_text: str,
    *,
    source: str,
    slot_ms: int,
    tgt_lang: str = "ru",
    use_llm: bool = True,
    max_attempts: int = MAX_ATTEMPTS,
    allowed_compression: float = 0.35,
    allowed_expansion: float = 0.15,
) -> RetryResult:
    """
    Up to max_attempts intelligent shorten/expand cycles.
    Fallback: semantic_text + warning if all fail.
    """
    decision_log: list[str] = []
    current = str(semantic_text or "").strip()
    original_ms = predict_duration_ms(current, tgt_lang)
    direction = needs_adaptation(original_ms, slot_ms)
    fit_threshold = max(
        0.72,
        SLOT_FIT_THRESHOLD - (0.35 - allowed_compression) * 0.15 + (0.15 - allowed_expansion) * 0.05,
    )
    decision_log.append(
        f"brief_fit_threshold={fit_threshold:.3f} compression={allowed_compression:.2f}"
    )

    rule_used = False
    llm_used = False
    best_score = 0.0
    best_text = current
    best_pred = original_ms
    best_variant = "A"
    all_candidates: list[dict] = []

    if direction == "none":
        fit = validate_slot_fit(original_ms, slot_ms)
        return RetryResult(
            text=current,
            predicted_ms=original_ms,
            slot_fit_score=fit.score,
            attempts=1,
            selected_variant="ORIGINAL",
            decision_log=["no_adaptation_needed"],
            candidates=[{"label": "ORIGINAL", "text": current, "predicted_ms": original_ms}],
        )

    for attempt in range(max_attempts):
        decision_log.append(f"attempt={attempt + 1} direction={direction}")
        selection = generate_and_select(
            current,
            source=source,
            slot_ms=slot_ms,
            tgt_lang=tgt_lang,
            use_llm=use_llm,
        )
        rule_used = rule_used or selection.rule_rewrite_used
        llm_used = llm_used or selection.llm_rewrite_used
        decision_log.extend(selection.decision_log)

        candidate = selection.best
        integrity = validate_sentence_integrity(semantic_text, candidate.text)
        if not integrity.ok:
            decision_log.append(
                f"reject variant={candidate.variant} integrity={integrity.issues}"
            )
            continue

        all_candidates.extend(
            {
                "label": c.variant,
                "text": c.text,
                "predicted_ms": c.predicted_ms,
                "slot_fit_score": c.scores.slot_fit,
            }
            for c in selection.candidates
        )

        if candidate.scores.overall > best_score:
            best_score = candidate.scores.overall
            best_text = candidate.text
            best_pred = candidate.predicted_ms
            best_variant = candidate.variant

        fit = validate_slot_fit(candidate.predicted_ms, slot_ms)
        if fit.score >= fit_threshold and integrity.ok:
            return RetryResult(
                text=candidate.text,
                predicted_ms=candidate.predicted_ms,
                slot_fit_score=fit.score,
                attempts=attempt + 1,
                selected_variant=candidate.variant,
                rule_rewrite_used=rule_used,
                llm_rewrite_used=llm_used,
                decision_log=decision_log,
                candidates=all_candidates,
            )

        current = candidate.text
        direction = needs_adaptation(candidate.predicted_ms, slot_ms)

    fit = validate_slot_fit(best_pred, slot_ms)
    micro_stretch = fit.overflow_ms > 0 and fit.score < fit_threshold
    used_fallback = False

    if fit.score < fit_threshold:
        decision_log.append("final_aggressive_shorten")
        from engines.ai_core.timing_agent.rule_rewrite import generate_shorten_candidates

        best_fit_score = fit.score
        for variant in generate_shorten_candidates(current, tgt_lang=tgt_lang).values():
            cand = str(variant or "").strip()
            if not cand:
                continue
            pred = predict_duration_ms(cand, tgt_lang)
            cand_fit = validate_slot_fit(pred, slot_ms)
            if cand_fit.score > best_fit_score:
                best_fit_score = cand_fit.score
                best_text = cand
                best_pred = pred
                best_variant = "rule_aggressive"
        fit = validate_slot_fit(best_pred, slot_ms)

    if fit.score < 0.5:
        decision_log.append("fallback_semantic_text")
        best_text = semantic_text
        best_pred = original_ms
        best_variant = "fallback"
        used_fallback = True
        fit = validate_slot_fit(best_pred, slot_ms)

    return RetryResult(
        text=best_text,
        predicted_ms=best_pred,
        slot_fit_score=fit.score,
        attempts=max_attempts,
        selected_variant=best_variant,
        rule_rewrite_used=rule_used,
        llm_rewrite_used=llm_used,
        micro_stretch_recommended=micro_stretch,
        used_fallback=used_fallback,
        decision_log=decision_log,
        candidates=all_candidates,
    )
