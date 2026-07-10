"""Generate 3 variants A/B/C, score, pick best."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.grammar_agent.llm_rewriter import llm_polish
from engines.ai_core.grammar_agent.rule_engine import generate_rule_candidates
from engines.ai_core.grammar_agent.scoring import CandidateScore, SegmentScores, score_candidate
from engines.ai_core.grammar_agent.validators.meaning_preservation import (
    validate_meaning_preservation,
)


@dataclass
class SelectionResult:
    best: CandidateScore
    candidates: list[CandidateScore]
    llm_used: bool = False
    rule_rewrite_used: bool = True
    decision_log: list[str] = field(default_factory=list)


def generate_candidates(
    source: str,
    timing_text: str,
    *,
    tgt_lang: str = "ru",
    use_llm: bool = True,
) -> tuple[dict[str, str], bool]:
    """Generate at least 3 candidate texts. Returns (variants_dict, llm_used)."""
    rule_variants = generate_rule_candidates(timing_text, tgt_lang=tgt_lang)
    llm_used = False

    if use_llm:
        llm_text, llm_ok = llm_polish(source, timing_text, tgt_lang=tgt_lang)
        if llm_text and llm_text.strip():
            rule_variants["LLM"] = llm_text.strip()
            llm_used = llm_ok

    values = list(rule_variants.values())
    while len(values) < 3:
        values.append(timing_text)

    return rule_variants, llm_used


def select_best_candidate(
    source: str,
    timing_text: str,
    variants: dict[str, str],
    *,
    tgt_lang: str = "ru",
    llm_used: bool = False,
) -> SelectionResult:
    """Score all variants and pick the best by weighted overall score."""
    scored: list[CandidateScore] = []
    for label, text in variants.items():
        if not str(text or "").strip():
            continue
        kind = "llm" if label == "LLM" else "rule"
        item = score_candidate(
            source,
            timing_text,
            text,
            variant=label,
            tgt_lang=tgt_lang,
            source_kind=kind,
        )
        if item is not None:
            scored.append(item)

    if not scored:
        fallback_text = str(timing_text or "").strip()
        fallback = score_candidate(
            source,
            timing_text,
            fallback_text,
            variant="fallback",
            tgt_lang=tgt_lang,
        )
        if fallback is None:
            fallback = CandidateScore(
                variant="fallback",
                text=fallback_text,
                scores=SegmentScores(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5),
                meaning_detail=validate_meaning_preservation(
                    source, timing_text, fallback_text
                ),
                length_ratio=1.0,
                source="fallback",
            )
        return SelectionResult(
            best=fallback,
            candidates=[fallback],
            llm_used=False,
            rule_rewrite_used=False,
            decision_log=["no_valid_candidates_fallback_timing_text"],
        )

    best = max(scored, key=lambda c: c.scores.overall)
    decision_log = [
        f"selected={best.variant} overall={best.scores.overall:.3f} "
        f"meaning={best.scores.meaning:.3f} len_ratio={best.length_ratio:.3f}"
    ]
    return SelectionResult(
        best=best,
        candidates=scored,
        llm_used=llm_used,
        rule_rewrite_used=any(c.source == "rule" for c in scored),
        decision_log=decision_log,
    )
