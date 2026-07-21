"""Timing Reviewer."""

from __future__ import annotations

import time
from typing import Any

from engines.tqe.models import ConfidenceMetrics, ReviewStatus, RetryStrategyName
from engines.tqe.reviewers.base import BaseReviewer
from engines.tqe.rules._registry import load_all_rules


class TimingReviewer(BaseReviewer):
    name = "TimingReviewer"

    def review(
        self,
        *,
        index: int,
        original: str,
        translation: str,
        context: dict[str, Any] | None = None,
    ):
        t0 = time.perf_counter()
        rules = load_all_rules()
        fn = rules.get("timing")
        errors = list(fn(original, translation, dict(context or {})) if fn else [])
        critical = [e for e in errors if e.get("severity") == "critical"]
        # Over-compression is reject-worthy for long sources
        if any(e.get("code") == "over_compressed" for e in errors):
            status = ReviewStatus.REJECT
        elif critical:
            status = ReviewStatus.REJECT
        elif errors:
            status = ReviewStatus.WARN
        else:
            status = ReviewStatus.PASS
        fitness = 1.0 if not errors else max(0.0, 1.0 - 0.3 * len(errors))
        conf = ConfidenceMetrics(timing_fitness=fitness)
        return self._timed(
            status=status,
            errors=errors,
            explanation="; ".join(e.get("code", "") for e in errors) or "timing_ok",
            confidence=conf,
            retry_strategy=(
                RetryStrategyName.TIMING.value
                if status == ReviewStatus.REJECT
                else RetryStrategyName.NONE.value
            ),
            t0=t0,
            metadata={"index": index, "slot_ms": (context or {}).get("slot_ms")},
        )
