"""
Multi-stage semantic optimization with time budget (TZ Semantic Translation §1–4).

Priority: preserve meaning → natural shorter phrasing → fit slot duration.
Never tail-clip; never use character count as primary criterion.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.semantic_optimizer")

FIT_TOLERANCE = 1.04
_SLOT_PADDING_MS = 40

STAGE_FILLERS = "stage_1_fillers"
STAGE_COMPACT = "stage_2_compact"
STAGE_RESTRUCTURE = "stage_3_restructure"
STAGE_SYNONYMS = "stage_4_synonyms"
STAGE_MINIMAL = "stage_5_minimal_removal"

STAGE_ORDER = (
    STAGE_FILLERS,
    STAGE_COMPACT,
    STAGE_RESTRUCTURE,
    STAGE_SYNONYMS,
    STAGE_MINIMAL,
)

# Rule-based only — no LLM, no tail word removal (TZ: mark requires_llm_adaptation instead).
RULE_ONLY_STAGES = (
    STAGE_FILLERS,
    STAGE_COMPACT,
    STAGE_SYNONYMS,
)


@dataclass
class TimeBudget:
    segment_duration_ms: int
    tts_estimated_ms: int
    delta_ms: int
    target_ms: int
    fits: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StageLogEntry:
    stage: str
    stage_num: int
    text_before: str
    text_after: str
    words_before: int
    words_after: int
    estimated_ms_before: int
    estimated_ms_after: int
    applied: bool
    reason: str
    information_removed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticOptimizationResult:
    text: str
    changed: bool
    budget: TimeBudget
    stages: list[StageLogEntry] = field(default_factory=list)
    meaning_loss_score: float = 0.0
    entity_preservation_score: float = 1.0
    compression_ratio: float = 1.0
    stopped_reason: str = ""
    information_removed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "changed": self.changed,
            "budget": self.budget.to_dict(),
            "stages": [s.to_dict() for s in self.stages],
            "meaning_loss_score": self.meaning_loss_score,
            "entity_preservation_score": self.entity_preservation_score,
            "compression_ratio": self.compression_ratio,
            "stopped_reason": self.stopped_reason,
            "information_removed": self.information_removed,
        }


def word_count(text: str) -> int:
    return len(str(text or "").split())


def compute_time_budget(
    text: str,
    slot_ms: int,
    *,
    tgt_lang: str,
) -> TimeBudget:
    from engines.semantic_adaptation import estimate_tts_duration_ms

    segment_duration = max(0, int(slot_ms or 0))
    target_ms = max(200, segment_duration - _SLOT_PADDING_MS)
    estimated = estimate_tts_duration_ms(text, tgt_lang)
    delta = max(0, estimated - target_ms)
    fits = estimated <= int(target_ms * FIT_TOLERANCE) if target_ms > 0 else True
    return TimeBudget(
        segment_duration_ms=segment_duration,
        tts_estimated_ms=estimated,
        delta_ms=delta,
        target_ms=target_ms,
        fits=fits,
    )


def _apply_stage(
    stage: str,
    stage_num: int,
    text: str,
    *,
    source_hint: str,
    tgt_lang: str,
    target_ratio: float,
    allow_llm: bool,
) -> tuple[str, StageLogEntry]:
    from engines.semantic_adaptation import estimate_tts_duration_ms
    from engines.translation_adapt import (
        _stage_minimal,
        _stage_moderate,
        _stage_semantic_rephrase,
    )

    before = " ".join(str(text or "").split())
    est_before = estimate_tts_duration_ms(before, tgt_lang)
    after = before
    reason = "unchanged"
    info_removed = False

    if stage == STAGE_FILLERS:
        after = _stage_minimal(before)
        reason = "removed_fillers" if after != before else "no_fillers"

    elif stage == STAGE_COMPACT:
        from engines.semantic_meaning import apply_compact_phrases
        from engines.translation_adapt import _SHORTEN_PATTERNS
        import re

        after = apply_compact_phrases(before, target_lang=tgt_lang)
        for pattern, repl in _SHORTEN_PATTERNS:
            after = re.sub(pattern, repl, after, flags=re.IGNORECASE)
        after = " ".join(after.split())
        reason = "compact_phrases" if after != before else "no_compact"

    elif stage == STAGE_RESTRUCTURE:
        after = _stage_semantic_rephrase(
            before,
            target_ratio,
            source_hint=source_hint,
            tgt_lang=tgt_lang,
        )
        if after == before and allow_llm:
            after = _stage_moderate(before)
            if after != before:
                reason = "grammar_simplify"
            else:
                reason = "restructure_skipped"
        else:
            reason = "restructured" if after != before else "restructure_skipped"

    elif stage == STAGE_SYNONYMS:
        from engines.semantic_meaning import apply_compact_phrases

        after = apply_compact_phrases(before, target_lang=tgt_lang)
        after = _stage_moderate(after)
        reason = "shorter_synonyms" if after != before else "no_synonyms"

    elif stage == STAGE_MINIMAL:
        from engines.translation_adapt import adapt_translation_shorter

        after = adapt_translation_shorter(
            before,
            target_ratio=max(0.55, target_ratio),
            source_hint=source_hint,
            allow_llm=allow_llm,
            stage="strong",
            tgt_lang=tgt_lang,
        )
        if after != before:
            reason = "minimal_removal"
            info_removed = True

    est_after = estimate_tts_duration_ms(after, tgt_lang)
    return after, StageLogEntry(
        stage=stage,
        stage_num=stage_num,
        text_before=before,
        text_after=after,
        words_before=word_count(before),
        words_after=word_count(after),
        estimated_ms_before=est_before,
        estimated_ms_after=est_after,
        applied=after != before,
        reason=reason,
        information_removed=info_removed,
    )


def optimize_rule_based_only(
    text: str,
    *,
    source_hint: str,
    slot_ms: int,
    tgt_lang: str,
) -> SemanticOptimizationResult:
    """
    Rule-based shortening only — no LLM, no tail word deletion.
    If text still does not fit → requires_llm_adaptation, return original unchanged.
    """
    from engines.semantic_adaptation import estimate_tts_duration_ms
    from engines.semantic_meaning import (
        compute_entity_preservation_score,
        compute_meaning_loss_score,
        verify_meaning_preserved,
    )

    original = " ".join(str(text or "").split())
    if not original:
        budget = compute_time_budget("", slot_ms, tgt_lang=tgt_lang)
        return SemanticOptimizationResult(
            text="",
            changed=False,
            budget=budget,
            stopped_reason="empty",
        )

    budget = compute_time_budget(original, slot_ms, tgt_lang=tgt_lang)
    if budget.fits:
        return SemanticOptimizationResult(
            text=original,
            changed=False,
            budget=budget,
            stopped_reason="fits_no_change",
            meaning_loss_score=0.0,
            entity_preservation_score=1.0,
            compression_ratio=1.0,
        )

    current = original
    stages: list[StageLogEntry] = []
    target_ratio = max(
        0.55,
        min(0.98, budget.target_ms / max(budget.tts_estimated_ms, 1)),
    )

    for num, stage_name in enumerate(RULE_ONLY_STAGES, start=1):
        candidate, entry = _apply_stage(
            stage_name,
            num,
            current,
            source_hint=source_hint,
            tgt_lang=tgt_lang,
            target_ratio=target_ratio,
            allow_llm=False,
        )
        stages.append(entry)
        if candidate == current:
            continue
        ok, reason, _ = verify_meaning_preserved(
            source_hint, original, candidate, target_lang=tgt_lang
        )
        if not ok:
            logger.info(
                "[SemanticOpt] rule stage=%s rejected meaning: %s",
                stage_name,
                reason,
            )
            break
        current = candidate
        budget = compute_time_budget(current, slot_ms, tgt_lang=tgt_lang)
        if budget.fits:
            return SemanticOptimizationResult(
                text=current,
                changed=current != original,
                budget=budget,
                stages=stages,
                stopped_reason=f"fits_after_{stage_name}",
                meaning_loss_score=compute_meaning_loss_score(
                    source_hint, original, current
                ),
                entity_preservation_score=compute_entity_preservation_score(
                    source_hint, current
                ),
                compression_ratio=round(word_count(current) / max(word_count(original), 1), 3),
            )
        target_ratio = max(
            0.55,
            min(0.98, budget.target_ms / max(budget.tts_estimated_ms, 1)),
        )

    # Rule-based could not fit — do NOT keep partial cuts; mark for LLM.
    return SemanticOptimizationResult(
        text=original,
        changed=False,
        budget=budget,
        stages=stages,
        stopped_reason="requires_llm_adaptation",
        meaning_loss_score=compute_meaning_loss_score(source_hint, original, original),
        entity_preservation_score=compute_entity_preservation_score(source_hint, original),
        compression_ratio=1.0,
    )


def optimize_llm_rephrase_for_slot(
    text: str,
    *,
    source_hint: str,
    slot_ms: int,
    tgt_lang: str,
    max_rounds: int = 3,
    current_ms: int | None = None,
) -> SemanticOptimizationResult:
    """
  LLM/full-sentence rephrase loop — preserve meaning, never tail-clip.
  Used after TTS duration measurement (post-TTS retry path).

  ``current_ms`` is the REAL measured TTS duration. When provided it drives the
  fit/overflow decision instead of the (often wrong) length estimate — this is
  the whole reason the post-TTS pass exists. Without it the estimate could claim
  a segment "fits" while the actual audio overflows, silently skipping the LLM.
    """
    from engines.semantic_adaptation import estimate_tts_duration_ms
    from engines.semantic_meaning import (
        compute_entity_preservation_score,
        compute_meaning_loss_score,
        verify_meaning_preserved,
    )
    from engines.translation_adapt import _stage_semantic_rephrase

    original = " ".join(str(text or "").split())
    budget = compute_time_budget(original, slot_ms, tgt_lang=tgt_lang)
    if not original:
        return SemanticOptimizationResult(text="", changed=False, budget=budget, stopped_reason="empty")

    # Prefer the measured duration over the estimate (post-TTS truth source).
    measured_ms = int(current_ms) if current_ms and current_ms > 0 else int(budget.tts_estimated_ms)
    tolerance_ms = int(budget.target_ms * FIT_TOLERANCE)
    if measured_ms <= tolerance_ms:
        return SemanticOptimizationResult(
            text=original,
            changed=False,
            budget=budget,
            stopped_reason="fits_no_change",
        )

    current = original
    stages: list[StageLogEntry] = []
    stopped = "requires_llm_adaptation"
    # Reduction target is driven by the measured overflow, not the estimate.
    overflow_ref_ms = max(measured_ms, int(budget.tts_estimated_ms), 1)

    for round_ in range(1, max(1, max_rounds) + 1):
        target_ratio = max(
            0.55,
            min(0.92, budget.target_ms / overflow_ref_ms - 0.03 * round_),
        )
        candidate = _stage_semantic_rephrase(
            current,
            target_ratio,
            source_hint=source_hint,
            tgt_lang=tgt_lang,
        )
        if candidate and candidate.strip() == current:
            # Only flag no_rewrite when LLM actually returned text (not timeout/error).
            try:
                from engines.translation_adapt import get_llm_calls, record_llm_no_rewrite

                last = (get_llm_calls() or [])[-1:] or [None]
                last_call = last[0]
                if (
                    last_call
                    and last_call.get("usable")
                    and str(last_call.get("received") or "").strip()
                ):
                    record_llm_no_rewrite("identical_output")
            except Exception:
                pass
            stopped = "no_rewrite_performed"
            break
        if not candidate:
            stopped = "llm_no_change"
            break
        # Quality gate after each rewrite (audit §8): meaning + sentence
        # integrity. On failure DO NOT give up — try another round (re-send to
        # LLM) until max_rounds, never emit a broken/meaning-changed variant.
        ok, reason, _ = verify_meaning_preserved(
            source_hint, original, candidate, target_lang=tgt_lang
        )
        if not ok:
            stopped = f"llm_meaning_rejected_{reason}"
            continue
        try:
            from engines.sentence_integrity import validate_tts_text

            integrity_ok, integrity_issues = validate_tts_text(candidate, tgt_lang=tgt_lang)
        except Exception:
            integrity_ok, integrity_issues = True, []
        if not integrity_ok:
            stopped = "llm_integrity_rejected_" + (integrity_issues[0] if integrity_issues else "invalid")
            continue
        est_before = estimate_tts_duration_ms(current, tgt_lang)
        est_after = estimate_tts_duration_ms(candidate, tgt_lang)
        stages.append(
            StageLogEntry(
                stage="llm_rephrase",
                stage_num=round_,
                text_before=current,
                text_after=candidate,
                words_before=word_count(current),
                words_after=word_count(candidate),
                estimated_ms_before=est_before,
                estimated_ms_after=est_after,
                applied=True,
                reason="llm_rephrase",
            )
        )
        current = candidate
        budget = compute_time_budget(current, slot_ms, tgt_lang=tgt_lang)
        if budget.fits:
            stopped = "fits_after_llm"
            break

    meaning_loss = compute_meaning_loss_score(source_hint, original, current)
    entity_score = compute_entity_preservation_score(source_hint, current)
    ow = word_count(original)
    cw = word_count(current)

    return SemanticOptimizationResult(
        text=current,
        changed=current != original,
        budget=budget,
        stages=stages,
        meaning_loss_score=meaning_loss,
        entity_preservation_score=entity_score,
        compression_ratio=round(cw / ow, 3) if ow else 1.0,
        stopped_reason=stopped,
    )


# Below this fill ratio a segment is "too short" for its slot and may be
# expanded by natural rephrasing (TZ §3). Never expand with fillers/padding.
EXPAND_TRIGGER_RATIO = 0.70
EXPAND_TARGET_RATIO = 0.92


def optimize_expand_for_slot(
    text: str,
    *,
    source_hint: str,
    slot_ms: int,
    tgt_lang: str,
    max_rounds: int = 2,
    current_ms: int | None = None,
) -> SemanticOptimizationResult:
    """Lengthen a too-short line via natural rephrase (TZ §3) — no fillers.

    Only acts when the estimated (or measured post-TTS) speech is well under the
    slot and an LLM endpoint is available. Otherwise returns the text unchanged.
    """
    from engines.semantic_adaptation import estimate_tts_duration_ms
    from engines.semantic_meaning import (
        compute_entity_preservation_score,
        compute_meaning_loss_score,
        verify_meaning_preserved,
    )
    from engines.translation_adapt import _llm_expand, llm_rephrase_available

    original = " ".join(str(text or "").split())
    budget = compute_time_budget(original, slot_ms, tgt_lang=tgt_lang)
    if not original or slot_ms <= 0:
        return SemanticOptimizationResult(text=original, changed=False, budget=budget, stopped_reason="empty")

    measured_ms = int(current_ms) if current_ms and current_ms > 0 else budget.tts_estimated_ms
    target_ms = budget.target_ms

    if current_ms and current_ms > 0:
        from engines.segment_timing_qa import (
            DURATION_MATCH_GOAL_MS,
            SPEECH_UNDERFLOW_EXPAND_MS,
        )

        gap_ms = slot_ms - measured_ms
        if gap_ms < SPEECH_UNDERFLOW_EXPAND_MS:
            return SemanticOptimizationResult(
                text=original,
                changed=False,
                budget=budget,
                stopped_reason="no_expand_needed",
            )
        target_ms = max(
            measured_ms + SPEECH_UNDERFLOW_EXPAND_MS // 2,
            slot_ms - max(DURATION_MATCH_GOAL_MS, int(gap_ms * 0.35)),
        )
    elif target_ms <= 0 or budget.tts_estimated_ms >= int(target_ms * EXPAND_TRIGGER_RATIO):
        return SemanticOptimizationResult(
            text=original, changed=False, budget=budget, stopped_reason="no_expand_needed"
        )
    if not llm_rephrase_available():
        # Natural lengthening is impossible without an LLM; never pad with fillers.
        return SemanticOptimizationResult(
            text=original, changed=False, budget=budget, stopped_reason="requires_llm_expansion"
        )

    current = original
    stages: list[StageLogEntry] = []
    stopped = "requires_llm_expansion"
    for round_ in range(1, max(1, max_rounds) + 1):
        if current_ms and current_ms > 0:
            target_ratio = min(0.98, target_ms / max(slot_ms, 1))
        else:
            target_ratio = EXPAND_TARGET_RATIO + 0.05 * round_
        candidate = _llm_expand(current, source_hint, target_ratio, tgt_lang=tgt_lang)
        if not candidate:
            stopped = "llm_no_change"
            break
        candidate = " ".join(candidate.split())
        if candidate == current:
            stopped = "llm_no_change"
            break
        # Reject weak-model corruption (foreign script leaking into a non-CJK dub).
        try:
            from engines.sentence_integrity import contains_foreign_script

            if contains_foreign_script(candidate, tgt_lang):
                stopped = "llm_foreign_script_rejected"
                break
        except Exception:
            pass
        ok, reason, _ = verify_meaning_preserved(
            source_hint, original, candidate, target_lang=tgt_lang
        )
        if not ok:
            stopped = f"llm_meaning_rejected_{reason}"
            break
        est_before = estimate_tts_duration_ms(current, tgt_lang)
        est_after = estimate_tts_duration_ms(candidate, tgt_lang)
        # Do not overshoot the slot — keep within fit tolerance.
        if est_after > int(target_ms * FIT_TOLERANCE):
            stopped = "expand_would_overflow"
            break
        stages.append(
            StageLogEntry(
                stage="llm_expand",
                stage_num=round_,
                text_before=current,
                text_after=candidate,
                words_before=word_count(current),
                words_after=word_count(candidate),
                estimated_ms_before=est_before,
                estimated_ms_after=est_after,
                applied=True,
                reason="llm_expand",
            )
        )
        current = candidate
        budget = compute_time_budget(current, slot_ms, tgt_lang=tgt_lang)
        stopped = "expanded_to_fit"
        if budget.tts_estimated_ms >= int(target_ms * EXPAND_TRIGGER_RATIO):
            break

    ow = word_count(original)
    cw = word_count(current)
    return SemanticOptimizationResult(
        text=current,
        changed=current != original,
        budget=budget,
        stages=stages,
        meaning_loss_score=compute_meaning_loss_score(source_hint, original, current),
        entity_preservation_score=compute_entity_preservation_score(source_hint, current),
        compression_ratio=round(cw / ow, 3) if ow else 1.0,
        stopped_reason=stopped,
    )


def optimize_for_time_budget(
    text: str,
    *,
    source_hint: str,
    slot_ms: int,
    tgt_lang: str,
    src_lang: str = "",
    allow_minimal_removal: bool = True,
    allow_llm: bool = True,
) -> SemanticOptimizationResult:
    """
    Gradual optimization driven by TTS time budget.
    Rule-based stages first; if still too long → requires_llm_adaptation (no word deletion).
    Optional LLM rephrase when allow_llm=True.
    """
    rule_result = optimize_rule_based_only(
        text,
        source_hint=source_hint,
        slot_ms=slot_ms,
        tgt_lang=tgt_lang,
    )
    if rule_result.stopped_reason != "requires_llm_adaptation":
        return rule_result

    if not allow_llm:
        return rule_result

    # Audit §7: at least Rewrite 1 → predict → Rewrite 2 → predict → Rewrite 3 →
    # predict → choose best. Each round re-predicts duration and keeps the first
    # variant that fits while preserving meaning (never a single attempt).
    llm_result = optimize_llm_rephrase_for_slot(
        text,
        source_hint=source_hint,
        slot_ms=slot_ms,
        tgt_lang=tgt_lang,
        max_rounds=3,
    )
    if llm_result.changed:
        return llm_result
    return rule_result


def build_transformation_chain(
    *,
    original: str,
    raw_mt: str,
    semantic: str,
    final_tts: str,
    slot_ms: int = 0,
    tgt_lang: str = "",
    optimization: SemanticOptimizationResult | None = None,
    actual_tts_ms: int = 0,
) -> dict[str, Any]:
    """OpenDDF transformation chain metrics (TZ §9)."""
    from engines.semantic_adaptation import estimate_tts_duration_ms
    from engines.semantic_meaning import (
        compute_entity_preservation_score,
        compute_meaning_loss_score,
        compute_semantic_validation_metrics,
    )

    def _node(label: str, text: str) -> dict[str, Any]:
        wc = word_count(text)
        est = estimate_tts_duration_ms(text, tgt_lang) if text and tgt_lang else 0
        return {
            "stage": label,
            "word_count": wc,
            "char_count": len(text),
            "estimated_speech_ms": est,
            "text_preview": text[:200],
        }

    chain = [
        _node("original", original),
        _node("raw_mt", raw_mt),
        _node("semantic", semantic),
        _node("final_tts", final_tts),
    ]

    ow = word_count(original)
    fw = word_count(final_tts)
    compression = round(fw / ow, 3) if ow else 1.0

    validation = compute_semantic_validation_metrics(
        original, raw_mt, semantic, final_tts
    )

    return {
        "chain": chain,
        "original_word_count": word_count(original),
        "raw_mt_word_count": word_count(raw_mt),
        "semantic_word_count": word_count(semantic),
        "final_tts_word_count": fw,
        "compression_ratio": compression,
        "segment_duration_ms": slot_ms,
        "estimated_speech_duration_ms": estimate_tts_duration_ms(final_tts, tgt_lang),
        "real_speech_duration_ms": actual_tts_ms,
        "meaning_loss_score": validation["meaning_loss_score"],
        "meaning_preservation_score": validation["meaning_preservation_score"],
        "entity_preservation_score": validation["entity_preservation_score"],
        "fact_preservation_score": validation["fact_preservation_score"],
        "naturalness_score": validation["naturalness_score"],
        "readability_score": validation["readability_score"],
        "aggregate_score": validation["aggregate_score"],
        "raw_mt_divergence": validation.get("raw_mt_divergence"),
        "change_reasons": validation.get("change_reasons") or [],
        "optimization_stages": (
            [s.to_dict() for s in optimization.stages] if optimization else []
        ),
        "optimization": optimization.to_dict() if optimization else None,
    }
