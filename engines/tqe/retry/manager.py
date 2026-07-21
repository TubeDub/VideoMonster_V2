"""Retry Manager — Strategy Pattern (no single retry prompt)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from engines.tqe.models import RetryStrategyName, SegmentQualityDecision


class RetryStrategy(ABC):
    name: str = "none"

    @abstractmethod
    def build_prompt(self, decision: SegmentQualityDecision) -> str:
        raise NotImplementedError


class MeaningPreservationStrategy(RetryStrategy):
    name = RetryStrategyName.MEANING_PRESERVATION.value

    def build_prompt(self, decision: SegmentQualityDecision) -> str:
        return (
            "Rewrite the translation to preserve ALL events, causes, effects, "
            "time order and negations from the original. Do not invent facts.\n"
            f"Original:\n{decision.original}\n"
            f"Bad translation:\n{decision.translation}\n"
            f"Problems:\n{decision.explanation}\n"
            "Return only the corrected translation."
        )


class EntityCorrectionStrategy(RetryStrategy):
    name = RetryStrategyName.ENTITY_CORRECTION.value

    def build_prompt(self, decision: SegmentQualityDecision) -> str:
        missing = []
        for r in decision.reports:
            for e in r.errors:
                if e.get("token"):
                    missing.append(str(e["token"]))
        return (
            "Correct the translation so ALL named entities are preserved "
            f"(especially: {', '.join(missing[:12]) or 'names/brands/numbers'}).\n"
            f"Original:\n{decision.original}\n"
            f"Translation:\n{decision.translation}\n"
            "Return only the corrected translation."
        )


class CompletionStrategy(RetryStrategy):
    name = RetryStrategyName.COMPLETION.value

    def build_prompt(self, decision: SegmentQualityDecision) -> str:
        return (
            "Complete the translation into a full grammatical sentence that covers "
            "the whole original meaning. No trailing fragments.\n"
            f"Original:\n{decision.original}\n"
            f"Incomplete translation:\n{decision.translation}\n"
            "Return only the completed translation."
        )


class GrammarStrategy(RetryStrategy):
    name = RetryStrategyName.GRAMMAR.value

    def build_prompt(self, decision: SegmentQualityDecision) -> str:
        return (
            "Fix grammar, agreement and remove orphan clause glue "
            "(no trailing fragments like job/experience tails).\n"
            f"Original:\n{decision.original}\n"
            f"Translation:\n{decision.translation}\n"
            f"Issues:\n{decision.explanation}\n"
            "Return only the corrected translation."
        )


class TimingStrategy(RetryStrategy):
    name = RetryStrategyName.TIMING.value

    def build_prompt(self, decision: SegmentQualityDecision) -> str:
        return (
            "Adjust length to fit the speaking slot while keeping full meaning. "
            "Do not delete events just to shorten.\n"
            f"Original:\n{decision.original}\n"
            f"Translation:\n{decision.translation}\n"
            "Return only the adjusted translation."
        )


class NarrativeStrategy(RetryStrategy):
    name = RetryStrategyName.NARRATIVE.value

    def build_prompt(self, decision: SegmentQualityDecision) -> str:
        return (
            "Restore missing story beats so the narrative stays continuous and clear.\n"
            f"Original:\n{decision.original}\n"
            f"Translation:\n{decision.translation}\n"
            "Return only the restored translation."
        )


_STRATEGIES: dict[str, RetryStrategy] = {
    s.name: s
    for s in (
        MeaningPreservationStrategy(),
        EntityCorrectionStrategy(),
        CompletionStrategy(),
        GrammarStrategy(),
        TimingStrategy(),
        NarrativeStrategy(),
    )
}


class RetryManager:
    def strategy_for(self, decision: SegmentQualityDecision) -> RetryStrategy | None:
        return _STRATEGIES.get(decision.retry_strategy)

    def build_prompt(self, decision: SegmentQualityDecision) -> str | None:
        strat = self.strategy_for(decision)
        return strat.build_prompt(decision) if strat else None

    def apply_once(
        self,
        decision: SegmentQualityDecision,
        *,
        llm_fn=None,
    ) -> dict[str, Any]:
        """Attempt one retry via provided llm_fn(prompt)->str. Returns result dict."""
        prompt = self.build_prompt(decision)
        if not prompt:
            return {"ok": False, "reason": "no_strategy", "text": decision.translation}
        if llm_fn is None:
            return {
                "ok": False,
                "reason": "no_llm",
                "prompt": prompt,
                "text": decision.translation,
                "strategy": decision.retry_strategy,
            }
        try:
            text = str(llm_fn(prompt) or "").strip()
            return {
                "ok": bool(text),
                "text": text or decision.translation,
                "strategy": decision.retry_strategy,
                "prompt": prompt,
            }
        except Exception as exc:
            return {
                "ok": False,
                "reason": str(exc),
                "text": decision.translation,
                "strategy": decision.retry_strategy,
            }
