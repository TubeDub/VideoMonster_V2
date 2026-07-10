"""AI Adaptation Engine — central intelligent text adaptation for TubeDub AutoDub.

Architecture (P0 rewrite):
  Rule Rewrite is PREP ONLY — it never makes the final decision.
  All overflow / meaning-risk / integrity-failure segments MUST pass through
  LLM with multi-variant scoring (3–5 rounds, 3 strategies per round).
  A hard gate blocks TTS when requires_llm_adaptation && !llm_called.

Pipeline per segment:
  Original → MT → Semantic → Rule Prep → Duration Predict →
  LLM Rewrite (multi-variant) → Meaning/Grammar/Integrity/Entity/Slot checks →
  Choose Best → TTS
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.ai_adaptation_engine")

MIN_LLM_ROUNDS = 3
MAX_LLM_ROUNDS = 5
_VARIANTS_PER_ROUND = 3
_CPU_STRATEGIES = ("shorten", "restructure", "compact")
MIN_VARIANTS_PER_SEGMENT = 10
MAX_ADAPTATION_ITERATIONS = 10


# AI Core may override the adaptation profile (variant counts, rounds) per dub
# run — AI Core is the single decision maker (it decides how many variants a
# project needs). None → the default v4 quality-first profile.
_PROFILE_OVERRIDE: dict[str, Any] | None = None


def set_adaptation_profile_override(profile: dict[str, Any] | None) -> None:
    """Install (or clear) an AI-Core-decided adaptation profile for this run.

    Recognised keys: min_rounds, max_rounds, variants_per_round, min_variants,
    max_variants, cpu_mode. Unknown/None values fall back to the defaults.
    """
    global _PROFILE_OVERRIDE
    if profile is None:
        _PROFILE_OVERRIDE = None
        return
    _PROFILE_OVERRIDE = {k: v for k, v in profile.items() if v is not None}


def adaptation_profile() -> dict[str, Any]:
    """v4 profile: quality-first; never below the minimum generated variants.

    AI Core can override this per run via ``set_adaptation_profile_override``.
    """
    base = {
        "min_rounds": 4,
        "max_rounds": MAX_ADAPTATION_ITERATIONS,
        "variants_per_round": _VARIANTS_PER_ROUND,
        "cpu_mode": False,
        "min_variants": MIN_VARIANTS_PER_SEGMENT,
        # Decision Engine policy (Task 3/10). Default is "problem_only": run the
        # expensive LLM only for segments the rule prep cannot fit — never for
        # every segment. AI Core overrides this per quality profile:
        #   off          → fast     (rule-based only, no LLM)
        #   problem_only → balanced (LLM only for problem segments)
        #   always       → max_quality (LLM for every overflow segment)
        "llm_policy": "problem_only",
    }
    if _PROFILE_OVERRIDE:
        base.update(_PROFILE_OVERRIDE)
    return base


@dataclass
class VariantScores:
    meaning: float = 0.0
    timing: float = 0.0
    grammar: float = 0.0
    naturalness: float = 0.0
    emotion: float = 0.0
    entity: float = 0.0
    sentence_integrity: float = 0.0
    lip_sync: float = 0.0
    slot_fit: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class RewriteVariant:
    text: str
    strategy: str
    prompt: str = ""
    response: str = ""
    scores: VariantScores = field(default_factory=VariantScores)
    selected: bool = False
    rejected_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "strategy": self.strategy,
            "prompt": self.prompt[:500],
            "response": self.response[:500],
            "scores": self.scores.to_dict(),
            "selected": self.selected,
            "rejected_reason": self.rejected_reason,
        }


@dataclass
class SegmentAdaptationTrace:
    index: int
    original: str = ""
    raw_translation: str = ""
    rule_prep_text: str = ""
    variants: list[RewriteVariant] = field(default_factory=list)
    chosen_text: str = ""
    chosen_reason: str = ""
    rejected_variants: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: int = 0
    llm_total_ms: float = 0.0
    requires_llm: bool = False
    llm_called: bool = False
    llm_skip_reason: str = ""
    rule_fallback_applied: bool = False
    iterations: int = 0
    validation_passed: bool = False
    meaning_score: float = 0.0
    slot_fit_score: float = 0.0
    naturalness_score: float = 0.0
    stages: list[dict[str, Any]] = field(default_factory=list)
    # AI Core Timeline / audit (Task 1/12): wall-clock span + top-level strategy.
    started_at: float = 0.0        # epoch seconds
    ended_at: float = 0.0          # epoch seconds
    total_ms: float = 0.0          # whole-segment adaptation wall time
    strategy_class: str = ""       # none | rule_rewrite | quick_llm | full_llm
    attempts: int = 0              # LLM attempts (rounds actually run)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "original": self.original[:400],
            "raw_translation": self.raw_translation[:400],
            "rule_prep_text": self.rule_prep_text[:400],
            "variants": [v.to_dict() for v in self.variants],
            "chosen_text": self.chosen_text[:400],
            "chosen_reason": self.chosen_reason,
            "rejected_variants": self.rejected_variants,
            "llm_calls": self.llm_calls,
            "llm_total_ms": round(self.llm_total_ms, 1),
            "requires_llm": self.requires_llm,
            "llm_called": self.llm_called,
            "llm_skip_reason": self.llm_skip_reason,
            "rule_fallback_applied": self.rule_fallback_applied,
            "iterations": self.iterations,
            "validation_passed": self.validation_passed,
            "meaning_score": self.meaning_score,
            "slot_fit_score": self.slot_fit_score,
            "naturalness_score": self.naturalness_score,
            "stages": self.stages,
            "started_at": round(self.started_at, 3),
            "ended_at": round(self.ended_at, 3),
            "total_ms": round(self.total_ms, 1),
            "strategy_class": self.strategy_class,
            "attempts": self.attempts,
            "error": self.error,
        }


@dataclass
class AdaptationResult:
    text: str
    changed: bool
    trace: SegmentAdaptationTrace
    requires_llm_adaptation: bool = False
    llm_called: bool = False
    stopped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "changed": self.changed,
            "trace": self.trace.to_dict(),
            "requires_llm_adaptation": self.requires_llm_adaptation,
            "llm_called": self.llm_called,
            "stopped_reason": self.stopped_reason,
        }


@dataclass
class AdaptationGateResult:
    passed: bool
    violations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "violations": self.violations}


def _word_count(text: str) -> int:
    return len(str(text or "").split())


def _text_similarity(a: str, b: str) -> float:
    aa = " ".join(str(a or "").lower().split())
    bb = " ".join(str(b or "").lower().split())
    if not aa and not bb:
        return 1.0
    if not aa or not bb:
        return 0.0
    sa = set(aa.split())
    sb = set(bb.split())
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / max(1, len(sa | sb))


def _likely_hallucination(candidate: str, *, source_hint: str, literal_translation: str) -> bool:
    src = str(source_hint or "")
    lit = str(literal_translation or "")
    cand = str(candidate or "")
    if not cand.strip():
        return False

    # New numbers in the candidate that do not exist in either source or literal MT.
    num_re = re.compile(r"\b\d[\d.,]*\b")
    src_nums = set(num_re.findall(src)) | set(num_re.findall(lit))
    cand_nums = set(num_re.findall(cand))
    if any(n not in src_nums for n in cand_nums):
        return True

    # Added proper nouns not present in source/literal usually indicate invented facts.
    ent_re = re.compile(r"\b[А-ЯІЇЄA-Z][\w'’-]{2,}\b")
    known = {e.lower() for e in ent_re.findall(src)} | {e.lower() for e in ent_re.findall(lit)}
    cand_entities = [e for e in ent_re.findall(cand)]
    for i, ent in enumerate(cand_entities):
        if i == 0:
            continue
        if ent.lower() not in known:
            return True
    return False


def _emotion_score(source_hint: str, candidate: str) -> float:
    src = str(source_hint or "").strip()
    cand = str(candidate or "").strip()
    if not cand:
        return 0.0
    src_q = src.count("?")
    src_exc = src.count("!")
    cand_q = cand.count("?")
    cand_exc = cand.count("!")
    punct_match = 1.0 if (src_q > 0) == (cand_q > 0) and (src_exc > 0) == (cand_exc > 0) else 0.7
    return punct_match


def _score_variant(
    candidate: str,
    *,
    original: str,
    source_hint: str,
    literal_translation: str,
    slot_ms: int,
    tgt_lang: str,
) -> tuple[VariantScores, str]:
    """Score one LLM variant across all quality dimensions (audit §7/§8)."""
    from engines.semantic_adaptation import estimate_tts_duration_ms
    from engines.semantic_meaning import (
        compute_entity_preservation_score,
        compute_meaning_loss_score,
        verify_meaning_preserved,
    )
    from engines.semantic_optimizer import compute_time_budget
    from engines.sentence_integrity import validate_tts_text

    cand = str(candidate or "").strip()
    if not cand:
        return VariantScores(), "empty"
    if _likely_hallucination(
        cand, source_hint=source_hint, literal_translation=literal_translation
    ):
        return VariantScores(), "hallucination"

    ok_meaning, reason, _ = verify_meaning_preserved(
        source_hint,
        literal_translation or original,
        cand,
        target_lang=tgt_lang,
    )
    meaning_loss = compute_meaning_loss_score(source_hint, original, cand)
    meaning = max(0.0, 1.0 - meaning_loss) if ok_meaning else 0.0
    if not ok_meaning:
        return VariantScores(meaning=meaning), f"meaning:{reason}"

    entity = compute_entity_preservation_score(source_hint, cand)

    integrity_ok, integrity_issues = validate_tts_text(cand)
    grammar = 1.0 if integrity_ok else 0.0
    sentence_integrity = grammar
    if not integrity_ok:
        return VariantScores(
            meaning=meaning,
            entity=entity,
            grammar=grammar,
            sentence_integrity=sentence_integrity,
        ), (
            "integrity:" + (integrity_issues[0] if integrity_issues else "invalid")
        )

    budget = compute_time_budget(cand, slot_ms, tgt_lang=tgt_lang)
    est = budget.tts_estimated_ms
    target = max(1, budget.target_ms)
    if budget.fits:
        timing = 1.0
        slot_fit = 1.0
    else:
        overflow_ratio = est / target
        timing = max(0.0, 1.0 - (overflow_ratio - 1.0) * 2.0)
        slot_fit = max(0.0, min(1.0, target / max(est, 1)))
        if overflow_ratio > 1.4:
            return VariantScores(
                meaning=meaning,
                timing=timing,
                grammar=grammar,
                naturalness=0.0,
                emotion=0.0,
                entity=entity,
                sentence_integrity=sentence_integrity,
                lip_sync=max(0.0, min(1.0, slot_fit)),
                slot_fit=slot_fit,
            ), "timing_hard_overflow"

    ow = max(1, _word_count(original))
    cw = _word_count(cand)
    ratio = cw / ow
    if 0.55 <= ratio <= 1.05:
        naturalness = 1.0
    elif ratio < 0.55:
        naturalness = max(0.0, ratio / 0.55)
    else:
        naturalness = max(0.0, 1.0 - (ratio - 1.05) * 0.5)
    emotion = _emotion_score(source_hint, cand)
    lip_sync = max(0.0, min(1.0, slot_fit))

    total = (
        meaning * 0.25
        + timing * 0.18
        + grammar * 0.10
        + naturalness * 0.12
        + emotion * 0.08
        + entity * 0.12
        + sentence_integrity * 0.08
        + lip_sync * 0.03
        + slot_fit * 0.04
    )
    return VariantScores(
        meaning=round(meaning, 3),
        timing=round(timing, 3),
        grammar=round(grammar, 3),
        naturalness=round(naturalness, 3),
        emotion=round(emotion, 3),
        entity=round(entity, 3),
        sentence_integrity=round(sentence_integrity, 3),
        lip_sync=round(lip_sync, 3),
        slot_fit=round(slot_fit, 3),
        total=round(total, 3),
    ), ""


def _llm_restructure(
    text: str,
    source_hint: str,
    target_ratio: float,
    tgt_lang: str,
) -> tuple[str | None, str]:
    """Full sentence restructure via LLM (audit §4/§5)."""
    from engines.translation_adapt import _lang_label
    from engines.ai_core import llm_gateway

    if not text.strip():
        return None, ""
    pct = int(max(55, min(95, target_ratio * 100)))
    lang = _lang_label(tgt_lang)
    prompt = (
        f"You are a professional dubbing editor for {lang} voice-over.\n"
        f"Completely RESTRUCTURE this dubbing line so it fits ~{pct}% speaking time.\n"
        "ALLOWED: change word order, grammar, split/merge clauses, synonyms, "
        "active voice, natural idioms.\n"
        "FORBIDDEN: delete meaning, names, actions, places, times, numbers; "
        "cut words mid-way; leave incomplete sentences; add new facts.\n"
        "Output ONLY the rewritten line, no quotes or explanation.\n"
    )
    if source_hint.strip():
        prompt += f"Original speech: {source_hint.strip()}\n"
    prompt += f"Current {lang} line: {text.strip()}"
    result = llm_gateway.chat(prompt, max_tokens=512, temperature=0.35)
    return result, prompt


def _llm_strategy_rewrite(
    text: str,
    *,
    source_hint: str,
    literal_translation: str,
    target_ratio: float,
    tgt_lang: str,
    strategy: str,
) -> tuple[str | None, str]:
    from engines.translation_adapt import _lang_label
    from engines.ai_core import llm_gateway

    lang = _lang_label(tgt_lang)
    pct = int(max(55, min(145, target_ratio * 100)))
    prompt = (
        f"You are a professional dubbing editor for {lang}. "
        f"Rewrite the line using strategy '{strategy}' to match about {pct}% speaking time.\n"
        "Keep full meaning. Preserve all names, places, numbers, dates, brands and organizations.\n"
        "Do not invent facts. Do not move events from other segments.\n"
        "Always output a COMPLETE grammatical sentence. No ellipsis endings.\n"
        "Output only rewritten line.\n"
    )
    if source_hint.strip():
        prompt += f"Original English: {source_hint.strip()}\n"
    if literal_translation.strip():
        prompt += f"Literal translation: {literal_translation.strip()}\n"
    prompt += f"Current line: {text.strip()}"
    out = llm_gateway.chat(prompt, max_tokens=512, temperature=0.45)
    return out, prompt


def _generate_round_variants(
    text: str,
    *,
    source_hint: str,
    literal_translation: str,
    slot_ms: int,
    tgt_lang: str,
    round_num: int,
    original: str,
    profile: dict[str, Any] | None = None,
) -> list[RewriteVariant]:
    """Generate LLM variants for one adaptation round (audit §7)."""
    from engines.semantic_optimizer import compute_time_budget
    from engines.translation_adapt import (
        _llm_expand,
        _llm_shorten,
        mark_llm_needed,
        record_llm_no_rewrite,
    )

    profile = profile or adaptation_profile()
    mark_llm_needed()
    budget = compute_time_budget(text, slot_ms, tgt_lang=tgt_lang)
    base_ratio = budget.target_ms / max(budget.tts_estimated_ms, 1)
    target_ratio = max(0.55, min(1.25, base_ratio - 0.02 * round_num))

    strategies: list[tuple[str, Any]] = [
        ("shorten", lambda: _llm_shorten(text, source_hint, max(0.60, target_ratio), tgt_lang)),
        ("restructure", lambda: _llm_restructure(text, source_hint, target_ratio, tgt_lang)[0]),
        ("synonym_compact", lambda: _llm_strategy_rewrite(
            text,
            source_hint=source_hint,
            literal_translation=literal_translation,
            target_ratio=target_ratio,
            tgt_lang=tgt_lang,
            strategy="replace long constructions with concise natural synonyms",
        )[0]),
        ("active_voice", lambda: _llm_strategy_rewrite(
            text,
            source_hint=source_hint,
            literal_translation=literal_translation,
            target_ratio=target_ratio,
            tgt_lang=tgt_lang,
            strategy="prefer active voice and direct sentence structure",
        )[0]),
        ("split_sentence", lambda: _llm_strategy_rewrite(
            text,
            source_hint=source_hint,
            literal_translation=literal_translation,
            target_ratio=target_ratio,
            tgt_lang=tgt_lang,
            strategy="split one heavy sentence into shorter complete clauses",
        )[0]),
        ("merge_sentence", lambda: _llm_strategy_rewrite(
            text,
            source_hint=source_hint,
            literal_translation=literal_translation,
            target_ratio=target_ratio,
            tgt_lang=tgt_lang,
            strategy="merge short clauses into one natural fluent sentence",
        )[0]),
        ("verbify", lambda: _llm_strategy_rewrite(
            text,
            source_hint=source_hint,
            literal_translation=literal_translation,
            target_ratio=target_ratio,
            tgt_lang=tgt_lang,
            strategy="replace noun-heavy phrases with vivid verbs",
        )[0]),
        ("idiomatic", lambda: _llm_strategy_rewrite(
            text,
            source_hint=source_hint,
            literal_translation=literal_translation,
            target_ratio=target_ratio,
            tgt_lang=tgt_lang,
            strategy="use local idiomatic spoken phrasing while preserving facts",
        )[0]),
    ]
    if base_ratio > 1.02:
        expand_ratio = min(1.25, base_ratio + 0.05)
        strategies.append(("expand", lambda: _llm_expand(text, source_hint, expand_ratio, tgt_lang)))
    else:
        strategies.append(("compact", lambda: _llm_shorten(text, source_hint, max(0.60, target_ratio - 0.05), tgt_lang)))

    if profile.get("cpu_mode"):
        pick = _CPU_STRATEGIES[(round_num - 1) % len(_CPU_STRATEGIES)]
        strategies = [s for s in strategies if s[0] == pick] or strategies[:1]

    limit = max(int(profile.get("variants_per_round") or _VARIANTS_PER_ROUND), len(strategies))
    variants: list[RewriteVariant] = []
    for strategy, fn in strategies[:limit]:
        prompt = ""
        try:
            if strategy == "restructure":
                response, prompt = _llm_restructure(text, source_hint, target_ratio, tgt_lang)
            else:
                prompt = f"[{strategy}] ratio={target_ratio:.2f}"
                response = fn()
            if not response or not str(response).strip():
                variants.append(
                    RewriteVariant(
                        text="",
                        strategy=strategy,
                        prompt=prompt,
                        response="",
                        rejected_reason="empty_response",
                    )
                )
                continue
            cand = " ".join(str(response).split())
            if _text_similarity(cand, text.strip()) >= 0.95:
                record_llm_no_rewrite("identical_output")
                variants.append(
                    RewriteVariant(
                        text=cand,
                        strategy=strategy,
                        prompt=prompt,
                        response=cand,
                        rejected_reason="identical_to_input",
                    )
                )
                continue
            scores, reject = _score_variant(
                cand,
                original=original,
                source_hint=source_hint,
                literal_translation=literal_translation,
                slot_ms=slot_ms,
                tgt_lang=tgt_lang,
            )
            variants.append(
                RewriteVariant(
                    text=cand,
                    strategy=strategy,
                    prompt=prompt,
                    response=cand,
                    scores=scores,
                    rejected_reason=reject,
                )
            )
        except Exception as exc:
            logger.debug("[AIEngine] variant %s failed: %s", strategy, exc)
            variants.append(
                RewriteVariant(text="", strategy=strategy, rejected_reason=str(exc))
            )
    return variants


def _pick_best_variant(
    variants: list[RewriteVariant],
    *,
    min_total: float = 0.45,
) -> RewriteVariant | None:
    """Select highest-scoring valid variant (audit §7)."""
    valid = [v for v in variants if v.text and not v.rejected_reason and v.scores.total >= min_total]
    if not valid:
        return None
    best = max(valid, key=lambda v: v.scores.total)
    best.selected = True
    return best


def adapt_segment_ai(
    text: str,
    *,
    source_hint: str,
    raw_translation: str = "",
    slot_ms: int,
    tgt_lang: str,
    index: int = 0,
    min_rounds: int = MIN_LLM_ROUNDS,
    max_rounds: int = MAX_LLM_ROUNDS,
) -> AdaptationResult:
    """Main AI adaptation entry — LLM is the decision maker (audit §1/§5/§7/§8)."""
    from engines.semantic_adaptation import estimate_tts_duration_ms
    from engines.semantic_optimizer import compute_time_budget, optimize_rule_based_only
    from engines.repetition_guard import remove_repeated_sentences
    from engines.translation_adapt import (
        get_llm_calls,
        llm_rephrase_available,
        mark_llm_needed,
        record_llm_skip,
        reset_segment_llm_breaker,
        set_llm_context,
    )

    reset_segment_llm_breaker(index)
    _seg_t0 = time.monotonic()
    _seg_started_at = time.time()
    original = " ".join(str(text or "").split())
    deduped, rep_fixed = remove_repeated_sentences(original)
    if rep_fixed:
        original = deduped
    trace = SegmentAdaptationTrace(
        index=index,
        original=original,
        raw_translation=str(raw_translation or original),
    )
    trace.started_at = _seg_started_at

    def _finish(result: AdaptationResult) -> AdaptationResult:
        # Stamp the AI Core Timeline span on every exit path (Task 1/12).
        trace.ended_at = time.time()
        trace.total_ms = round((time.monotonic() - _seg_t0) * 1000.0, 1)
        if not trace.strategy_class:
            trace.strategy_class = (
                "full_llm" if trace.llm_called
                else ("rule_rewrite" if trace.chosen_text.strip() != original.strip()
                      else "none")
            )
        return result
    if rep_fixed:
        trace.stages.append(
            {"stage": "dedupe", "text_before": text, "text_after": original, "reason": "repeated_sentences"}
        )
    profile = adaptation_profile()
    budget = compute_time_budget(original, slot_ms, tgt_lang=tgt_lang)

    if not original or slot_ms <= 0:
        trace.chosen_text = original
        trace.chosen_reason = "empty_or_no_slot"
        trace.strategy_class = "none"
        return _finish(AdaptationResult(
            text=original, changed=False, trace=trace, stopped_reason="empty"
        ))

    if budget.fits:
        # Decision Engine (Task 3/10): already fits → NO expensive processing.
        trace.chosen_text = original
        trace.chosen_reason = "fits_no_change"
        trace.strategy_class = "none"
        trace.validation_passed = True
        trace.stages.append(
            {"stage": "decision", "strategy": "none", "reason": "fits_slot_no_rewrite"}
        )
        return _finish(AdaptationResult(
            text=original, changed=False, trace=trace, stopped_reason="fits_no_change"
        ))

    # ── Stage 1: Rule prep ONLY (never final decision — audit §8) ──
    rule_result = optimize_rule_based_only(
        original,
        source_hint=source_hint,
        slot_ms=slot_ms,
        tgt_lang=tgt_lang,
    )
    prep_text = rule_result.text
    trace.rule_prep_text = prep_text
    trace.stages.append(
        {
            "stage": "rule_prep",
            "text_before": original,
            "text_after": prep_text,
            "reason": rule_result.stopped_reason,
        }
    )
    prep_budget = compute_time_budget(prep_text, slot_ms, tgt_lang=tgt_lang)
    trace.stages.append(
        {
            "stage": "duration_predictor",
            "target_ms": prep_budget.target_ms,
            "estimated_ms": prep_budget.tts_estimated_ms,
            "fits": prep_budget.fits,
            "delta_ms": prep_budget.delta_ms,
        }
    )

    # ── Decision Engine (Task 3/10): pick the CHEAPEST strategy that works ──
    # AI Core is the dispatcher: do not fire the expensive LLM for every
    # segment. Policy comes from the quality profile:
    #   off          → fast: rule-based only, never LLM
    #   problem_only → balanced: LLM only when rule prep cannot fit
    #   always       → max_quality: LLM for every overflow segment
    llm_policy = str(profile.get("llm_policy") or "problem_only").strip().lower()

    if prep_budget.fits and llm_policy != "always":
        # Rule/timing rewrite already fits the slot → accept it, skip the LLM.
        trace.chosen_text = prep_text
        trace.chosen_reason = "rule_prep_fits"
        trace.strategy_class = "rule_rewrite"
        trace.validation_passed = True
        trace.stages.append(
            {"stage": "decision", "strategy": "rule_rewrite", "reason": "prep_fits_skip_llm"}
        )
        return _finish(AdaptationResult(
            text=prep_text,
            changed=prep_text.strip() != original.strip(),
            trace=trace,
            requires_llm_adaptation=False,
            llm_called=False,
            stopped_reason="rule_prep_fits",
        ))

    if llm_policy == "off":
        # Fast profile: never call the LLM. Keep the best rule-based result even
        # if it slightly overflows (timing layer/atempo will absorb the rest).
        trace.chosen_text = prep_text or original
        trace.chosen_reason = "fast_mode_rule_only"
        trace.strategy_class = "rule_rewrite"
        trace.validation_passed = prep_budget.fits
        trace.stages.append(
            {"stage": "decision", "strategy": "rule_rewrite", "reason": "fast_profile_no_llm"}
        )
        return _finish(AdaptationResult(
            text=prep_text or original,
            changed=(prep_text or original).strip() != original.strip(),
            trace=trace,
            requires_llm_adaptation=False,
            llm_called=False,
            stopped_reason="fast_mode_rule_only",
        ))

    # ── Stage 2: LLM rewrite (problem segment or max_quality) ──
    trace.requires_llm = True
    trace.strategy_class = "full_llm"
    trace.stages.append(
        {"stage": "decision", "strategy": "full_llm",
         "reason": "prep_overflow" if not prep_budget.fits else "max_quality_forced"}
    )
    mark_llm_needed(segment=index)
    set_llm_context(segment=index, stage="ai_adaptation_engine")

    if not llm_rephrase_available():
        record_llm_skip("no_endpoint")
        trace.llm_skip_reason = "no_endpoint"
        trace.llm_called = False
        # Rule-based aggressive shorten when LLM endpoint is missing (P0).
        chosen = prep_text or original
        if slot_ms and tgt_lang:
            try:
                from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
                from engines.ai_core.timing_agent.rule_rewrite import generate_shorten_candidates

                best = chosen
                best_ms = predict_duration_ms(best, tgt_lang)
                for variant in generate_shorten_candidates(
                    chosen, tgt_lang=tgt_lang
                ).values():
                    v = str(variant or "").strip()
                    if not v:
                        continue
                    ms = predict_duration_ms(v, tgt_lang)
                    if ms <= slot_ms * 1.05 and ms < best_ms:
                        best, best_ms = v, ms
                chosen = best
            except Exception:
                pass
        trace.chosen_text = chosen
        trace.chosen_reason = "llm_unavailable_rule_shorten"
        trace.rule_fallback_applied = True
        trace.strategy_class = "rule_rewrite"
        chosen_budget = compute_time_budget(chosen, slot_ms, tgt_lang=tgt_lang)
        return _finish(AdaptationResult(
            text=chosen,
            changed=chosen.strip() != original.strip(),
            trace=trace,
            requires_llm_adaptation=not chosen_budget.fits,
            llm_called=False,
            stopped_reason="rule_shorten_no_llm",
        ))

    # Circuit may already be open from earlier timeouts — same rule-shorten path.
    try:
        from engines.translation_adapt import circuit_open as _circuit_open

        if _circuit_open():
            record_llm_skip("llm_circuit_open")
            trace.llm_skip_reason = "llm_circuit_open"
            trace.llm_called = False
            chosen = prep_text or original
            if slot_ms and tgt_lang:
                try:
                    from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
                    from engines.ai_core.timing_agent.rule_rewrite import generate_shorten_candidates

                    best = chosen
                    best_ms = predict_duration_ms(best, tgt_lang)
                    for variant in generate_shorten_candidates(
                        chosen, tgt_lang=tgt_lang
                    ).values():
                        v = str(variant or "").strip()
                        if not v:
                            continue
                        ms = predict_duration_ms(v, tgt_lang)
                        if ms <= slot_ms * 1.05 and ms < best_ms:
                            best, best_ms = v, ms
                    chosen = best
                except Exception:
                    pass
            trace.chosen_text = chosen
            trace.chosen_reason = "llm_circuit_open_rule_shorten"
            trace.rule_fallback_applied = True
            trace.strategy_class = "rule_rewrite"
            chosen_budget = compute_time_budget(chosen, slot_ms, tgt_lang=tgt_lang)
            return _finish(AdaptationResult(
                text=chosen,
                changed=chosen.strip() != original.strip(),
                trace=trace,
                requires_llm_adaptation=not chosen_budget.fits,
                llm_called=False,
                stopped_reason="rule_shorten_circuit_open",
            ))
    except Exception:
        pass

    current = prep_text if prep_text else original
    best_overall: RewriteVariant | None = None
    eff_min = int(profile.get("min_rounds") or min_rounds)
    eff_max = int(profile.get("max_rounds") or max_rounds)
    rounds = max(eff_min, min(eff_max, MAX_ADAPTATION_ITERATIONS))
    min_variants = int(profile.get("min_variants") or MIN_VARIANTS_PER_SEGMENT)
    calls_before = len(get_llm_calls())
    start_ts = time.monotonic()

    for round_num in range(1, rounds + 1):
        trace.iterations = round_num
        round_variants = _generate_round_variants(
            current,
            source_hint=source_hint,
            literal_translation=trace.raw_translation or original,
            slot_ms=slot_ms,
            tgt_lang=tgt_lang,
            round_num=round_num,
            original=original,
            profile=profile,
        )
        trace.variants.extend(round_variants)
        for v in round_variants:
            if v.rejected_reason:
                trace.rejected_variants.append(
                    {"strategy": v.strategy, "reason": v.rejected_reason}
                )

        best_round = _pick_best_variant(round_variants, min_total=0.55)
        if best_round:
            if best_overall is None or best_round.scores.total > best_overall.scores.total:
                if best_overall:
                    best_overall.selected = False
                best_overall = best_round
                best_overall.selected = True
            round_budget = compute_time_budget(best_round.text, slot_ms, tgt_lang=tgt_lang)
            if round_budget.fits:
                trace.chosen_text = best_round.text
                trace.chosen_reason = f"llm_{best_round.strategy}_round{round_num}_fits"
                trace.validation_passed = True
                trace.meaning_score = best_round.scores.meaning
                trace.slot_fit_score = best_round.scores.slot_fit
                trace.naturalness_score = best_round.scores.naturalness
                break
            current = best_round.text
        if len(trace.variants) >= min_variants and round_num >= eff_min:
            if best_overall:
                est_budget = compute_time_budget(best_overall.text, slot_ms, tgt_lang=tgt_lang)
                if est_budget.fits:
                    break

    calls_after = len(get_llm_calls())
    trace.llm_calls = max(0, calls_after - calls_before)
    trace.llm_called = trace.llm_calls > 0
    calls_after_rows = get_llm_calls()[calls_before:]
    trace.llm_total_ms = round(sum(float(c.get("ms") or 0.0) for c in calls_after_rows), 1)
    if trace.llm_total_ms <= 0:
        trace.llm_total_ms = round((time.monotonic() - start_ts) * 1000.0, 1)

    if best_overall and not trace.chosen_text:
        trace.chosen_text = best_overall.text
        trace.chosen_reason = f"llm_best_{best_overall.strategy}_score_{best_overall.scores.total:.2f}"
        trace.meaning_score = best_overall.scores.meaning
        trace.slot_fit_score = best_overall.scores.slot_fit
        trace.naturalness_score = best_overall.scores.naturalness
        trace.validation_passed = best_overall.scores.grammar >= 1.0 and best_overall.scores.meaning >= 0.7

    if not trace.chosen_text:
        trace.chosen_text = prep_text or original
        trace.chosen_reason = "llm_exhausted_kept_prep"

    if trace.requires_llm and not trace.llm_called:
        from engines.translation_adapt import get_llm_status
        from engines.ai_core.llm_gateway import RULE_FALLBACK_REASONS

        status_rows = {s.get("segment"): s for s in get_llm_status()}
        st = status_rows.get(index, {})
        trace.llm_skip_reason = st.get("skip_reason") or "llm_not_called"
        if trace.llm_skip_reason in RULE_FALLBACK_REASONS or trace.chosen_reason in (
            "llm_exhausted_kept_prep",
            "llm_unavailable_kept_prep",
        ):
            # Aggressive rule shorten when LLM never ran (circuit / budget / etc.).
            chosen = trace.chosen_text or prep_text or original
            if slot_ms and tgt_lang and not trace.rule_fallback_applied:
                try:
                    from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
                    from engines.ai_core.timing_agent.rule_rewrite import generate_shorten_candidates

                    best = chosen
                    best_ms = predict_duration_ms(best, tgt_lang)
                    for variant in generate_shorten_candidates(
                        chosen, tgt_lang=tgt_lang
                    ).values():
                        v = str(variant or "").strip()
                        if not v:
                            continue
                        ms = predict_duration_ms(v, tgt_lang)
                        if ms <= slot_ms * 1.05 and ms < best_ms:
                            best, best_ms = v, ms
                    if best.strip() != chosen.strip():
                        chosen = best
                        trace.chosen_reason = "rule_shorten_after_llm_skip"
                except Exception:
                    pass
            trace.chosen_text = chosen
            trace.rule_fallback_applied = True
            if not trace.chosen_reason:
                trace.chosen_reason = "rule_fallback_after_llm_skip"
            trace.strategy_class = "rule_rewrite"

    trace.attempts = trace.iterations
    changed = trace.chosen_text.strip() != original.strip()
    chosen_budget = compute_time_budget(trace.chosen_text, slot_ms, tgt_lang=tgt_lang)
    still_needs = trace.requires_llm and (
        (not trace.llm_called) or (not chosen_budget.fits) or (not trace.validation_passed)
    )
    if trace.rule_fallback_applied and trace.chosen_text.strip():
        still_needs = False

    return _finish(AdaptationResult(
        text=trace.chosen_text,
        changed=changed,
        trace=trace,
        requires_llm_adaptation=still_needs or (
            trace.requires_llm and not trace.validation_passed and not trace.rule_fallback_applied
        ),
        llm_called=trace.llm_called,
        stopped_reason="requires_llm_adaptation" if still_needs else (
            trace.chosen_reason or "ai_adaptation_done"
        ),
    ))


def validate_pre_tts_checks(
    text: str,
    *,
    source_hint: str,
    original: str,
    slot_ms: int,
    tgt_lang: str,
) -> tuple[bool, list[str]]:
    """Full pre-TTS validation suite (audit §5/§6)."""
    from engines.pipeline_language_gate import is_critical_language_mismatch
    from engines.naturalizer_v2.bad_patterns import detect_bad_mt_patterns
    from engines.repetition_guard import has_repetition
    from engines.semantic_meaning import verify_meaning_preserved
    from engines.semantic_optimizer import compute_time_budget
    from engines.sentence_integrity import validate_tts_text

    issues: list[str] = []
    t = str(text or "").strip()
    if not t:
        return False, ["empty"]

    ok_int, int_issues = validate_tts_text(t)
    if not ok_int:
        issues.extend(int_issues)

    if has_repetition(t):
        issues.append("repetition")

    bad_mt = detect_bad_mt_patterns(t)
    if bad_mt:
        seen: set[str] = set()
        for hit in bad_mt[:3]:
            code = str(hit.get("code") or "").strip()
            if code and code not in seen:
                issues.append(f"bad_mt:{code}")
                seen.add(code)

    ok_meaning, reason, _ = verify_meaning_preserved(
        source_hint, original or t, t, target_lang=tgt_lang
    )
    if not ok_meaning:
        issues.append(f"meaning:{reason}")

    bad_lang, _ = is_critical_language_mismatch(t, target_lang=tgt_lang, original=source_hint)
    if bad_lang:
        issues.append("language_mismatch")

    budget = compute_time_budget(t, slot_ms, tgt_lang=tgt_lang)
    if not budget.fits and budget.delta_ms > 200:
        issues.append(f"timing_overflow:{budget.delta_ms}ms")

    return (not issues), issues


def enforce_adaptation_gate(
    segments: list[str],
    *,
    timing_records: list[dict[str, Any]] | None = None,
    llm_status: list[dict[str, Any]] | None = None,
    segments_data: list[dict[str, Any]] | None = None,
    llm_calls: list[dict[str, Any]] | None = None,
) -> AdaptationGateResult:
    """Gate BEFORE TTS — block only when LLM was required, not called, and no fallback text.

  Graceful LLM skips (circuit open, segment budget, breaker, no endpoint) with usable
  segment text are treated as rule-fallback satisfied and do not stop the pipeline.
    """
    from engines.ai_core.llm_gateway import RULE_FALLBACK_REASONS
    from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

    GRACEFUL_LLM_SKIPS = RULE_FALLBACK_REASONS | frozenset({
        "transport_unavailable",
        "blocked",
        "timeout",
    })

    violations: list[dict[str, Any]] = []
    status_by_seg = {s.get("segment"): s for s in (llm_status or [])}
    calls_by_seg: dict[Any, int] = {}
    for c in llm_calls or []:
        seg = c.get("segment")
        calls_by_seg[seg] = calls_by_seg.get(seg, 0) + 1

    record_by_idx = {r.get("index"): r for r in (timing_records or [])}

    for idx, _text in enumerate(segments):
        rec = record_by_idx.get(idx, {})
        st = status_by_seg.get(idx, {})
        seg_data = (segments_data or [{}])[idx] if idx < len(segments_data or []) else {}

        requires = bool(
            seg_data.get("requires_llm_adaptation")
            or rec.get("requires_llm_adaptation")
            or "requires_llm" in str(rec.get("reason") or "")
            or st.get("needed")
        )
        called = bool(
            calls_by_seg.get(idx, 0) > 0
            or st.get("called")
            or rec.get("llm_called")
            or seg_data.get("llm_called")
        )

        if requires and not called:
            skip_reason = str(
                st.get("skip_reason") or rec.get("llm_skip_reason") or ""
            ).strip()
            text_ok = bool(str(segments[idx] or "").strip())
            rule_fallback = bool(
                skip_reason in GRACEFUL_LLM_SKIPS
                or rec.get("rule_fallback_applied")
                or seg_data.get("rule_fallback_applied")
                or rec.get("strategy_class") in ("rule_rewrite", "none")
                or seg_data.get("adaptation_executed")
                or (skip_reason == "no_endpoint" and text_ok)
            )
            if IS_DEBUG_LEARNING_MODE() or (text_ok and rule_fallback):
                continue
            violations.append(
                {
                    "index": idx,
                    "code": "LLM_NOT_CALLED",
                    "reason": skip_reason or "unknown",
                    "message": (
                        f"Сегмент #{idx + 1} требует интеллектуальной адаптации, "
                        "но LLM не была вызвана — передача в TTS запрещена"
                    ),
                }
            )

    return AdaptationGateResult(passed=not violations, violations=violations)


def propagate_adaptation_flags(
    segments_data: list[dict[str, Any]],
    traces: list[SegmentAdaptationTrace],
) -> None:
    """Write adaptation flags + full trace onto segments_data (audit §3/§9)."""
    by_idx = {t.index: t for t in traces}
    for idx, seg in enumerate(segments_data):
        trace = by_idx.get(idx)
        if not trace:
            continue
        seg["requires_llm_adaptation"] = (
            trace.requires_llm and (not trace.llm_called or not trace.validation_passed)
            and not trace.rule_fallback_applied
        )
        seg["llm_called"] = trace.llm_called
        seg["rule_fallback_applied"] = trace.rule_fallback_applied
        seg["llm_attempts"] = trace.llm_calls
        seg["adaptation_executed"] = (
            trace.llm_called or trace.validation_passed or trace.rule_fallback_applied
        )
        seg["ai_adaptation_trace"] = trace.to_dict()
        if trace.requires_llm and not trace.llm_called:
            seg.setdefault("adaptation_errors", []).append(
                {
                    "code": "LLM_NOT_CALLED",
                    "reason": trace.llm_skip_reason or "unknown",
                }
            )
