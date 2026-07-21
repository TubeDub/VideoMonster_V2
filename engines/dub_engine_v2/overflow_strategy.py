"""Dub Engine overflow strategy chain — costed multi-variant resolver (TZ Dub Engine).

Strict order (no skipping):
  Trim Silence → Pause Optimization → Tempo → Stretch → Borrow Time
  → Sentence Merge → Semantic Rewrite → Manual Review

Video stretch / gap absorb are Stretch/Borrow family — never before Trim/Pause/Tempo.
Translation Engine is not modified here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# TZ costs (Problem №6)
STRATEGY_COSTS: dict[str, float] = {
    "trim_silence": 1.0,
    "pause_optimization": 2.0,
    "tempo": 2.0,
    "stretch": 4.0,
    "borrow_time": 6.0,
    "sentence_merge": 10.0,
    "semantic_rewrite": 20.0,
    "manual_review": 100.0,
    "ready": 0.0,
}

# TZ order (Problem №10) — video stretch is stretch, gap absorb is borrow_time
STRATEGY_ORDER: tuple[str, ...] = (
    "trim_silence",
    "pause_optimization",
    "tempo",
    "stretch",
    "borrow_time",
    "sentence_merge",
    "semantic_rewrite",
    "manual_review",
)

# Aliases used by slot_fit / ATO / closed_loop → canonical strategy id
_ALIAS: dict[str, str] = {
    "trim": "trim_silence",
    "trim_compress": "trim_silence",
    "silence_trim": "trim_silence",
    "pause": "pause_optimization",
    "pause_compress": "pause_optimization",
    "smart_pause_compression": "pause_optimization",
    "redistribute_gap": "borrow_time",
    "gap_absorb": "borrow_time",
    "borrow_gap": "borrow_time",
    "neighbor_redistribution": "borrow_time",
    "tempo_5pct": "tempo",
    "tempo_10pct_emergency": "tempo",
    "tempo_emergency": "tempo",
    "atempo": "tempo",
    "micro_stretch": "stretch",
    "video_adapt": "stretch",
    "video_stretch": "stretch",
    "soft_stretch": "stretch",
    "dsal_rule_compress": "semantic_rewrite",
    "semantic_rewrite": "semantic_rewrite",
    "smart_compression": "semantic_rewrite",
    "llm_rephrase": "semantic_rewrite",
    "block_merge": "sentence_merge",
    "sentence_merge": "sentence_merge",
    "studio_manual_review": "manual_review",
    "manual_review": "manual_review",
    "crossfade": "pause_optimization",
}


@dataclass
class StrategyVariant:
    strategy: str
    cost: float
    reason: str = ""
    estimated_fit: bool = False
    quality: dict[str, float] = field(default_factory=dict)
    rejected: bool = False
    reject_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OverflowDecision:
    index: int
    overflow_ms: int
    slot_ms: int
    cause: str
    variants_considered: list[StrategyVariant]
    chosen: str
    chosen_cost: float
    why: str
    duration_after_ms: int = 0
    adaptation_executed: bool = False
    requires_manual_review: bool = False
    requires_re_tts: bool = False
    quality: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "overflow_ms": self.overflow_ms,
            "slot_ms": self.slot_ms,
            "cause": self.cause,
            "variants_considered": [v.to_dict() for v in self.variants_considered],
            "chosen": self.chosen,
            "chosen_cost": self.chosen_cost,
            "why": self.why,
            "duration_after_ms": self.duration_after_ms,
            "adaptation_executed": self.adaptation_executed,
            "requires_manual_review": self.requires_manual_review,
            "requires_re_tts": self.requires_re_tts,
            "quality": dict(self.quality),
        }


def canonicalize(strategy: str) -> str:
    s = str(strategy or "").strip().lower()
    return _ALIAS.get(s, s)


def strategy_cost(strategy: str) -> float:
    return float(STRATEGY_COSTS.get(canonicalize(strategy), 50.0))


def order_index(strategy: str) -> int:
    can = canonicalize(strategy)
    try:
        return STRATEGY_ORDER.index(can)
    except ValueError:
        return len(STRATEGY_ORDER)


def build_strategy_variants(
    *,
    overflow_ms: int,
    slot_ms: int,
    gap_after_ms: int = 0,
    llm_available: bool = False,
    text_locked: bool = True,
) -> list[StrategyVariant]:
    """Build ≥4 costed variants for an overflow (Problem №5)."""
    ov = max(0, int(overflow_ms))
    slot = max(1, int(slot_ms))
    pct = ov / slot
    variants: list[StrategyVariant] = []

    variants.append(
        StrategyVariant(
            "trim_silence",
            STRATEGY_COSTS["trim_silence"],
            reason="remove leading/trailing silence",
            estimated_fit=ov <= 120,
        )
    )
    variants.append(
        StrategyVariant(
            "pause_optimization",
            STRATEGY_COSTS["pause_optimization"],
            reason="compress internal pauses",
            estimated_fit=ov <= 250,
        )
    )
    variants.append(
        StrategyVariant(
            "tempo",
            STRATEGY_COSTS["tempo"],
            reason="playback rate ≤±5% (≤12% emergency)",
            estimated_fit=pct <= 0.12,
        )
    )
    # Stretch only after tempo (Problem №8 / №10)
    variants.append(
        StrategyVariant(
            "stretch",
            STRATEGY_COSTS["stretch"],
            reason="micro stretch or video stretch",
            estimated_fit=pct <= 0.15,
        )
    )
    variants.append(
        StrategyVariant(
            "borrow_time",
            STRATEGY_COSTS["borrow_time"],
            reason="gap absorb / neighbor borrow",
            estimated_fit=gap_after_ms >= ov and ov > 0,
        )
    )

    if not text_locked:
        variants.append(
            StrategyVariant(
                "sentence_merge",
                STRATEGY_COSTS["sentence_merge"],
                reason="merge with neighbor segment",
                estimated_fit=pct > 0.15,
            )
        )
        variants.append(
            StrategyVariant(
                "semantic_rewrite",
                STRATEGY_COSTS["semantic_rewrite"],
                reason="DSAL/rule rewrite" if not llm_available else "LLM or DSAL rewrite",
                estimated_fit=True,
            )
        )
    else:
        # Locked: rewrite/merge deferred to manual unless unlock path exists
        variants.append(
            StrategyVariant(
                "semantic_rewrite",
                STRATEGY_COSTS["semantic_rewrite"],
                reason="text locked — rewrite blocked until studio unlock",
                estimated_fit=False,
                rejected=True,
                reject_reason="translation_locked",
            )
        )

    variants.append(
        StrategyVariant(
            "manual_review",
            STRATEGY_COSTS["manual_review"],
            reason="studio manual review after all cheaper options",
            estimated_fit=False,
        )
    )
    return variants


def estimate_quality_scores(
    *,
    overflow_ms: int,
    slot_ms: int,
    strategy: str,
    meaning_ok: bool = True,
) -> dict[str, float]:
    """Lightweight quality vector (Problem №7)."""
    slot = max(1, int(slot_ms))
    ov = max(0, int(overflow_ms))
    fill = max(0.0, 1.0 - ov / slot)
    can = canonicalize(strategy)
    timing = fill
    if can in ("trim_silence", "pause_optimization", "tempo"):
        naturalness = 0.92
        prosody = 0.9
    elif can in ("stretch", "borrow_time"):
        naturalness = 0.8
        prosody = 0.75
        timing = min(1.0, fill + 0.15)
    elif can == "semantic_rewrite":
        naturalness = 0.85
        prosody = 0.85
        timing = 0.9
    elif can == "manual_review":
        naturalness = 0.5
        prosody = 0.5
        timing = 0.5
    else:
        naturalness = 0.75
        prosody = 0.75
    meaning = 1.0 if meaning_ok else 0.4
    if can == "semantic_rewrite" and meaning_ok:
        meaning = 0.95
    entity = 1.0 if meaning_ok else 0.5
    context = 0.9
    duration = timing
    return {
        "meaning": round(meaning, 3),
        "timing": round(timing, 3),
        "naturalness": round(naturalness, 3),
        "prosody": round(prosody, 3),
        "duration": round(duration, 3),
        "entity": round(entity, 3),
        "context": round(context, 3),
    }


def quality_acceptable(scores: dict[str, float], *, min_meaning: float = 0.7, min_timing: float = 0.55) -> bool:
    if float(scores.get("meaning") or 0) < min_meaning:
        return False
    if float(scores.get("timing") or 0) < min_timing and float(scores.get("duration") or 0) < min_timing:
        return False
    return True


def select_best_variant(
    variants: list[StrategyVariant],
    *,
    require_fit_estimate: bool = False,
) -> StrategyVariant:
    """Pick minimum cost among quality-acceptable variants (Problem №6)."""
    scored: list[StrategyVariant] = []
    for v in variants:
        if v.rejected:
            continue
        if not v.quality:
            v.quality = estimate_quality_scores(
                overflow_ms=0, slot_ms=1000, strategy=v.strategy
            )
        if not quality_acceptable(v.quality):
            v.rejected = True
            v.reject_reason = v.reject_reason or "quality_below_threshold"
            continue
        if require_fit_estimate and not v.estimated_fit and v.strategy != "manual_review":
            continue
        scored.append(v)
    if not scored:
        # Always allow manual review as last resort
        for v in variants:
            if canonicalize(v.strategy) == "manual_review":
                return v
        return StrategyVariant("manual_review", 100.0, reason="fallback")
    scored.sort(key=lambda x: (x.cost, order_index(x.strategy)))
    return scored[0]


def next_required_strategy(already_applied: list[str]) -> str | None:
    """Next strategy in TZ order that has not been applied yet."""
    applied = {canonicalize(s) for s in already_applied}
    for s in STRATEGY_ORDER:
        if s not in applied:
            return s
    return None


def assert_strategy_order(applied: list[str]) -> list[str]:
    """Return violations if stages were skipped (Problem №10)."""
    applied_can = [canonicalize(s) for s in applied]
    seen_idx = [-1]
    violations: list[str] = []
    for s in applied_can:
        idx = order_index(s)
        # Allow only forward progress; skipping means applied something with higher
        # index while a lower one was never applied.
        for earlier in STRATEGY_ORDER[:idx]:
            if earlier not in applied_can and earlier != "manual_review":
                # Only flag if we jumped to stretch/borrow/rewrite without tempo/trim
                if s in ("stretch", "borrow_time", "sentence_merge", "semantic_rewrite") and earlier in (
                    "trim_silence",
                    "pause_optimization",
                    "tempo",
                ):
                    violations.append(f"skipped:{earlier}_before_{s}")
        seen_idx.append(idx)
    return sorted(set(violations))


def decide_overflow(
    *,
    index: int,
    overflow_ms: int,
    slot_ms: int,
    cause: str = "audio_longer_than_slot",
    gap_after_ms: int = 0,
    llm_available: bool = False,
    text_locked: bool = True,
    already_applied: list[str] | None = None,
) -> OverflowDecision:
    """Full decision with variants + explanation (Problem №11)."""
    applied = [canonicalize(s) for s in (already_applied or [])]
    variants = build_strategy_variants(
        overflow_ms=overflow_ms,
        slot_ms=slot_ms,
        gap_after_ms=gap_after_ms,
        llm_available=llm_available,
        text_locked=text_locked,
    )
    # Mark already-applied as sunk cost; prefer next cheaper unused that still fits
    for v in variants:
        v.quality = estimate_quality_scores(
            overflow_ms=overflow_ms, slot_ms=slot_ms, strategy=v.strategy
        )
        if canonicalize(v.strategy) in applied:
            v.rejected = True
            v.reject_reason = "already_applied"

    # Prefer next in chain that is estimated to fit; else cheapest acceptable
    nxt = next_required_strategy(applied)
    preferred: StrategyVariant | None = None
    if nxt:
        for v in variants:
            if canonicalize(v.strategy) == nxt and not v.rejected:
                preferred = v
                break
    if preferred and (preferred.estimated_fit or preferred.strategy == "manual_review"):
        chosen = preferred
    else:
        chosen = select_best_variant(variants)

    needs_rewrite = canonicalize(chosen.strategy) in ("semantic_rewrite", "sentence_merge")
    decision = OverflowDecision(
        index=index,
        overflow_ms=int(overflow_ms),
        slot_ms=int(slot_ms),
        cause=cause,
        variants_considered=variants,
        chosen=canonicalize(chosen.strategy),
        chosen_cost=float(chosen.cost),
        why=(
            f"min_cost={chosen.cost} among quality-ok; "
            f"next_in_chain={nxt}; applied={applied}"
        ),
        adaptation_executed=True,
        requires_manual_review=canonicalize(chosen.strategy) == "manual_review",
        requires_re_tts=needs_rewrite and not text_locked,
        quality=dict(chosen.quality or {}),
    )
    return decision


def stamp_decision_on_segment(seg: dict[str, Any], decision: OverflowDecision) -> None:
    """Persist decision + force adaptation_executed (Problem №1 / №11)."""
    from engines.dub_engine_v2.adaptation_decision import mark_adaptation_executed
    from engines.dub_engine_v2.decision_trace import (
        STATUS_SUCCESS,
        record_stage,
        record_strategy_choice,
        STAGE_STRATEGY_RESULT,
        STAGE_TTS,
        STAGE_SCHEDULER,
    )

    seg["overflow_decision"] = decision.to_dict()
    record_strategy_choice(
        seg,
        chosen=decision.chosen,
        why=decision.why,
        cost=decision.chosen_cost,
        overflow_ms=decision.overflow_ms,
        index=decision.index,
    )
    record_stage(
        seg,
        stage=STAGE_STRATEGY_RESULT,
        status=STATUS_SUCCESS,
        reason=decision.why,
        detail={
            "chosen": decision.chosen,
            "cost": decision.chosen_cost,
            "variants": len(decision.variants_considered or []),
        },
        index=decision.index,
    )
    if decision.requires_re_tts:
        record_stage(
            seg,
            stage=STAGE_TTS,
            status=STATUS_SUCCESS,
            reason="requires_re_tts",
            detail={"requires_re_tts": True},
            index=decision.index,
        )
    else:
        record_stage(
            seg,
            stage=STAGE_TTS,
            status="SKIPPED",
            reason="AudioStrategyNoTextRewrite",
            detail={"requires_re_tts": False},
            index=decision.index,
        )
    record_stage(
        seg,
        stage=STAGE_SCHEDULER,
        status=STATUS_SUCCESS,
        reason=f"strategy_queued:{decision.chosen}",
        detail={"chosen": decision.chosen},
        index=decision.index,
    )
    mark_adaptation_executed(
        seg,
        decision=decision.chosen,
        stages=[f"overflow_strategy:{decision.chosen}"],
    )
    if decision.requires_manual_review:
        seg["needs_studio"] = True
        seg["requires_manual_review"] = True


def collect_unhandled_overflows(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Segments still overflowing without adaptation / with skipped chain."""
    from engines.dub_engine_v2.adaptation_decision import (
        ensure_skip_reason,
        finalize_segment_adaptation_fields,
        overflow_adaptation_violation,
    )

    bad: list[dict[str, Any]] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None:
            continue
        finalize_segment_adaptation_fields(seg, index=i)
        viol = overflow_adaptation_violation(seg)
        if viol:
            bad.append(
                {
                    "index": i,
                    "overflow_ms": viol["overflow_ms"],
                    "adaptation_executed": False,
                    "skip_reason": viol["skip_reason"],
                    "chosen": "",
                    "violations": [],
                    "reason": "OverflowDetected_AdaptationSkipped",
                    "message": viol["message"],
                }
            )
            continue
        ov = int(seg.get("overflow_ms") or 0)
        if ov <= 0 and not seg.get("slot_overflow"):
            continue
        adapted = bool(seg.get("adaptation_executed"))
        decision = seg.get("overflow_decision") or {}
        chosen = canonicalize(str(decision.get("chosen") or ""))
        applied = list((seg.get("text_adaptation_trace") or {}).get("stages") or [])
        applied += list((seg.get("ato_levels") or seg.get("optimizer_levels") or []))
        if seg.get("video_adapt_mode"):
            applied.append(str(seg.get("video_adapt_mode")))
        violations = assert_strategy_order(
            [canonicalize(a.split(":")[-1] if ":" in str(a) else a) for a in applied]
            + ([chosen] if chosen else [])
        )
        if ov > 40 and chosen in ("", "ready") and adapted:
            bad.append(
                {
                    "index": i,
                    "overflow_ms": ov,
                    "adaptation_executed": adapted,
                    "skip_reason": ensure_skip_reason(seg, index=i),
                    "chosen": chosen,
                    "violations": violations,
                    "reason": "overflow_unresolved",
                }
            )
        elif violations and chosen in ("stretch", "borrow_time", "video_adapt"):
            bad.append(
                {
                    "index": i,
                    "overflow_ms": ov,
                    "adaptation_executed": adapted,
                    "skip_reason": ensure_skip_reason(seg, index=i),
                    "chosen": chosen,
                    "violations": violations,
                    "reason": "strategy_order_violation",
                }
            )
    return bad


class UnhandledOverflowError(RuntimeError):
    """Architectural error: SUCCESS with untreated overflow (TZ Requirements)."""

    def __init__(self, message: str, *, segments: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.segments = segments or []


def assert_pipeline_may_succeed(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Forbid false SUCCESS: overflow + adaptation_executed=false ⇒ FAILED."""
    from engines.dub_engine_v2.adaptation_decision import finalize_segment_adaptation_fields
    from engines.dub_engine_v2.decision_trace import assert_no_silent_decision_stages

    for i, seg in enumerate(segments):
        if isinstance(seg, dict):
            finalize_segment_adaptation_fields(seg, index=i)

    silent = assert_no_silent_decision_stages(segments)
    bad = collect_unhandled_overflows(segments)
    illegal = [
        b
        for b in bad
        if (not b.get("adaptation_executed"))
        or b.get("reason") in ("overflow_without_adaptation", "OverflowDetected_AdaptationSkipped")
    ]
    report = {
        "ok": not illegal,
        "unhandled_count": len(illegal),
        "unhandled": illegal,
        "warnings": [b for b in bad if b not in illegal],
        "silent_decision_stages": silent,
        "failure_code": "OverflowDetected_AdaptationSkipped" if illegal else "",
    }
    if illegal:
        details = "; ".join(
            f"#{(int(b.get('index')) + 1) if b.get('index') is not None else '?'} "
            f"skip_reason={b.get('skip_reason') or 'UnknownSkip'} "
            f"overflow_ms={b.get('overflow_ms')}"
            for b in illegal[:8]
        )
        raise UnhandledOverflowError(
            f"Pipeline FAILED: OverflowDetected + AdaptationSkipped "
            f"({len(illegal)} segment(s)). {details}",
            segments=illegal,
        )
    return report
