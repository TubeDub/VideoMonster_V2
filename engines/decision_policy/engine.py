"""P302 Decision Policy Engine — chooses strategies only (no text/audio mutation)."""

from __future__ import annotations

import logging
from typing import Any

from engines.decision_policy.cache import cache_get, cache_put
from engines.decision_policy.config_loader import get_profile, list_profiles, load_policy_config
from engines.decision_policy.constraints import hard_constraint_check, safety_validate
from engines.decision_policy.estimator import (
    collect_confidences,
    decision_score,
    estimate_quality,
)
from engines.decision_policy.planner import generate_strategies
from engines.decision_policy.rollback import attach_rollback
from engines.decision_policy.timeline import detect_conflicts, plan_timeline
from engines.decision_policy.types import DecisionGraph, DecisionRecord
from engines.semantic_v3.types import SemanticSentence

logger = logging.getLogger("tubedub.decision_policy")


def resolve_profile_name(sentences: list[SemanticSentence], hint: str = "") -> str:
    if hint:
        return hint
    for s in sentences:
        st = getattr(s, "style", "") or ""
        if st:
            return st
    return "Movie"


def decide_sentence(
    sent: SemanticSentence,
    *,
    profile_name: str,
    cfg: dict[str, Any],
    use_cache: bool = True,
) -> DecisionRecord:
    """Produce a DecisionRecord — never mutates text/WAV fields."""
    profile = get_profile(profile_name, cfg)
    slot = sent.slot_ms
    expected = int(sent.predicted_tts_ms or getattr(sent, "estimated_duration", 0) or 0)
    overflow = max(0, expected - slot) if slot > 0 else 0
    problem = "overflow" if overflow > int(slot * 0.08) else "fit"

    if use_cache:
        cached = cache_get(
            problem=problem,
            profile=profile_name,
            overflow_ms=overflow,
            slot_ms=slot,
            cfg=cfg,
        )
        if cached is not None:
            cached.sentence_uuid = sent.sentence_uuid
            cached.confidences = collect_confidences(sent)
            return cached

    candidates = generate_strategies(sent, profile=profile, cfg=cfg)
    for cand in candidates:
        reasons = hard_constraint_check(sent, cand.steps, profile=profile)
        if reasons:
            cand.rejected = True
            cand.reject_reasons = reasons
            cand.explanation = "rejected:" + ",".join(reasons)
            continue
        cand.scores = estimate_quality(sent, cand, profile=profile, cfg=cfg)
        cand.decision_score = decision_score(cand.scores, cfg=cfg, profile=profile)
        cand.explanation = (
            f"steps={'>'.join(cand.steps)}; fit={cand.expected_fit}; "
            f"score={cand.decision_score}; cost={cand.cost}"
        )

    record = DecisionRecord(
        sentence_uuid=sent.sentence_uuid,
        problem=problem,
        profile=profile_name,
        candidates=candidates,
        confidences=collect_confidences(sent),
    )
    attach_rollback(record)

    # Explain rejected vs accepted
    if record.accepted:
        why_not = []
        for c in record.candidates:
            if c is record.accepted:
                continue
            if c.rejected:
                why_not.append(f"{c.label}:rejected({','.join(c.reject_reasons)})")
            else:
                why_not.append(
                    f"{c.label}:score={c.decision_score}<{record.accepted.decision_score}"
                )
        record.reason = record.reason + "; " + "; ".join(why_not[:6])

    cache_put(record, cfg=cfg, overflow_ms=overflow, slot_ms=slot)
    return record


def run_decision_policy(
    sentences: list[SemanticSentence],
    *,
    profile: str = "",
    container: dict[str, Any] | None = None,
    attach: bool = True,
) -> DecisionGraph:
    """
    Central strategist for post–Semantic Lock dub planning.
    Attaches decision metadata only — never changes translation text or audio.
    """
    safety_validate(container)
    cfg = load_policy_config()
    profile_name = resolve_profile_name(sentences, profile)
    if profile_name not in list_profiles(cfg):
        # normalize case
        for p in list_profiles(cfg):
            if p.lower() == profile_name.lower():
                profile_name = p
                break
        else:
            profile_name = "Movie"

    timeline = plan_timeline(sentences)
    conflicts = detect_conflicts(sentences)

    records: list[DecisionRecord] = []
    for s in sentences:
        # Snapshot text to prove immutability
        before = s.translated_text
        rec = decide_sentence(s, profile_name=profile_name, cfg=cfg)
        if s.translated_text != before:
            from engines.pipeline_integrity.exceptions import ArchitectureViolation

            raise ArchitectureViolation(
                "P302/P318: Decision Policy mutated translation text",
                stage="decision_policy",
                rule="no_text_mutation",
                segment_id=s.sentence_uuid,
            )
        if attach:
            # Metadata only
            setattr(s, "decision_record", rec)
            s.recovery_plan = list(rec.accepted.steps) if rec.accepted else list(s.recovery_plan)
            s.context = {
                **(s.context or {}),
                "decision": rec.to_dict(),
                "decision_explain": rec.reason,
            }
            # Compatibility AdaptivePlan-like flag
            from engines.semantic_v3.adaptive_planning import AdaptivePlan

            plan = AdaptivePlan(
                sentence_uuid=s.sentence_uuid,
                fits=bool(rec.accepted and rec.accepted.expected_fit) or rec.problem == "fit",
                expected_ms=int(s.predicted_tts_ms or 0),
                slot_ms=s.slot_ms,
                overflow_ms=max(0, int(s.predicted_tts_ms or 0) - s.slot_ms),
                decisions=list(rec.accepted.steps) if rec.accepted else [],
                tts_allowed="manual_review" not in (rec.accepted.steps if rec.accepted else []),
                reason=rec.reason[:200],
            )
            setattr(s, "adaptive_plan", plan)
        records.append(rec)

    graph = DecisionGraph(
        scene_uuid=next(
            (getattr(s, "scene_uuid", "") for s in sentences if getattr(s, "scene_uuid", "")),
            "",
        ),
        profile=profile_name,
        records=records,
        conflicts=conflicts,
        timeline_plan=timeline,
    )
    if conflicts:
        logger.warning("DecisionPolicy conflicts=%d", len(conflicts))
    logger.info(
        "DecisionPolicy: sentences=%d profile=%s strategies_avg=%.1f",
        len(sentences),
        profile_name,
        (
            sum(len(r.candidates) for r in records) / max(1, len(records))
        ),
    )
    return graph
