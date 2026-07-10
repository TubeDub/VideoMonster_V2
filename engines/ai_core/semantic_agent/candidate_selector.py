"""Generate 3 variants A/B/C, score, pick best."""

from __future__ import annotations

from dataclasses import dataclass, field

from engines.ai_core.semantic_agent.llm_rewriter import llm_rewrite
from engines.ai_core.semantic_agent.rule_engine import generate_rule_candidates
from engines.ai_core.semantic_agent.scoring import CandidateScore, score_candidate


@dataclass
class SelectionResult:
    best: CandidateScore
    candidates: list[CandidateScore]
    llm_used: bool = False
    rule_rewrite_used: bool = True
    decision_log: list[str] = field(default_factory=list)


def generate_candidates(
    source: str,
    translated: str,
    *,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    dialogue_block: str = "",
    use_llm: bool = True,
    app_dir=None,
) -> tuple[dict[str, str], bool]:
    """Generate at least 3 candidate texts. Returns (variants_dict, llm_used)."""
    rule_variants = generate_rule_candidates(
        translated,
        source=source,
        tgt_lang=tgt_lang,
        prev_context=prev_context,
        app_dir=app_dir,
    )
    llm_used = False

    if use_llm:
        llm_text, llm_ok = llm_rewrite(
            source,
            translated,
            tgt_lang=tgt_lang,
            prev_context=prev_context,
            dialogue_block=dialogue_block,
        )
        if llm_text and llm_text.strip():
            rule_variants["LLM"] = llm_text.strip()
            llm_used = llm_ok

    # Ensure minimum 3 distinct candidates
    values = list(rule_variants.values())
    while len(values) < 3:
        values.append(translated)

    return rule_variants, llm_used


def select_best_candidate(
    source: str,
    translated: str,
    variants: dict[str, str],
    *,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    llm_used: bool = False,
) -> SelectionResult:
    """Score all variants and pick the best by weighted overall score."""
    scored: list[CandidateScore] = []
    for label, text in variants.items():
        if not str(text or "").strip():
            continue
        kind = "llm" if label == "LLM" else "rule"
        scored.append(
            score_candidate(
                source,
                translated,
                text,
                variant=label,
                tgt_lang=tgt_lang,
                prev_context=prev_context,
                source_kind=kind,
            )
        )

    if not scored:
        fallback = score_candidate(
            source,
            translated,
            translated,
            variant="fallback",
            tgt_lang=tgt_lang,
            prev_context=prev_context,
        )
        return SelectionResult(
            best=fallback,
            candidates=[fallback],
            llm_used=False,
            rule_rewrite_used=False,
            decision_log=["no_candidates_fallback_translated"],
        )

    best = max(scored, key=lambda c: c.scores.overall)
    decision_log = [
        f"selected={best.variant} overall={best.scores.overall:.3f} "
        f"meaning={best.scores.meaning:.3f}"
    ]
    return SelectionResult(
        best=best,
        candidates=scored,
        llm_used=llm_used,
        rule_rewrite_used=any(c.source == "rule" for c in scored),
        decision_log=decision_log,
    )
