"""P303 Strategy Planner + P307 Multi-Strategy (≥4 candidates)."""

from __future__ import annotations

from typing import Any

from engines.decision_policy.config_loader import min_strategies, strategy_cost
from engines.decision_policy.types import StrategyCandidate
from engines.semantic_v3.types import SemanticSentence


def _overflow_ms(sent: SemanticSentence) -> int:
    slot = sent.slot_ms
    expected = int(sent.predicted_tts_ms or getattr(sent, "estimated_duration", 0) or 0)
    if slot <= 0:
        return 0
    return max(0, expected - slot)


def generate_strategies(
    sent: SemanticSentence,
    *,
    profile: dict[str, Any],
    cfg: dict[str, Any],
) -> list[StrategyCandidate]:
    """
    Build ≥4 distinct strategies. Does not execute them.
    Prefer profile.prefer order; always include low-cost and high-cost extremes.
    """
    overflow = _overflow_ms(sent)
    prefer = list(profile.get("prefer") or [])
    avoid = set(profile.get("avoid") or [])

    templates: list[tuple[str, list[str]]] = []
    if overflow <= 0:
        templates.append(("A", ["ready"]))
        templates.append(("B", ["trim_silence", "ready"]))
        templates.append(("C", ["pause_optimization", "ready"]))
        templates.append(("D", ["prosody", "ready"]))
    else:
        # Spec examples + profile-driven
        templates.append(("A", ["trim_silence", "tempo", "ready"]))
        templates.append(("B", ["borrow_time", "ready"]))
        templates.append(("C", ["sentence_merge", "tempo", "ready"]))
        templates.append(("D", ["trim_silence", "pause_optimization", "tempo", "ready"]))
        # Extra candidates from profile prefer
        if prefer:
            steps = [s for s in prefer if s not in avoid][:3] + ["ready"]
            templates.append(("E", steps))
        if "stretch" not in avoid:
            templates.append(("F", ["tempo", "stretch", "ready"]))
        if profile.get("allow_rewrite"):
            templates.append(("G", ["semantic_rewrite", "ready"]))
        else:
            templates.append(("G", ["borrow_time", "tempo", "ready"]))
        templates.append(("H", ["manual_review", "ready"]))

    # Deduplicate by step tuple, keep ≥ min_strategies
    seen: set[tuple[str, ...]] = set()
    out: list[StrategyCandidate] = []
    for label, steps in templates:
        key = tuple(steps)
        if key in seen:
            continue
        if any(s in avoid and s != "ready" for s in steps):
            # Still generate but mark later via constraints if rewrite
            if "semantic_rewrite" in steps and "semantic_rewrite" in avoid:
                continue
        seen.add(key)
        out.append(
            StrategyCandidate(
                label=label,
                steps=list(steps),
                cost=strategy_cost(steps, cfg),
            )
        )

    need = min_strategies(cfg)
    while len(out) < need:
        filler = ["trim_silence", "pause_optimization", "tempo", "ready"]
        # Vary filler
        extra = filler[: 1 + (len(out) % 3)] + ["ready"]
        key = tuple(extra)
        if key in seen:
            extra = ["prosody", "tempo", "ready"]
            key = tuple(extra)
        if key in seen:
            break
        seen.add(key)
        out.append(
            StrategyCandidate(
                label=chr(ord("A") + len(out)),
                steps=list(extra),
                cost=strategy_cost(extra, cfg),
            )
        )
    return out
