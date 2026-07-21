"""Grammar Reviewer — rejects orphan clause glue."""

from __future__ import annotations

import time
from typing import Any

from engines.tqe.models import ConfidenceMetrics, ReviewStatus, RetryStrategyName
from engines.tqe.reviewers.base import BaseReviewer
from engines.tqe.rules._registry import load_all_rules


class GrammarReviewer(BaseReviewer):
    name = "GrammarReviewer"

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
        errors: list[dict] = []
        for name in ("grammar", "sentence"):
            fn = rules.get(name)
            if fn:
                errors.extend(fn(original, translation, dict(context or {})) or [])

        critical = [e for e in errors if e.get("severity") == "critical"]
        status = ReviewStatus.REJECT if critical else (
            ReviewStatus.WARN if errors else ReviewStatus.PASS
        )
        conf = ConfidenceMetrics(
            grammar_integrity=0.0 if critical else (0.6 if errors else 1.0),
            sentence_completeness=0.0
            if any(e.get("code") == "incomplete_sentence" for e in errors)
            else 1.0,
        )
        return self._timed(
            status=status,
            errors=errors,
            explanation="; ".join(e.get("code", "") for e in errors) or "grammar_ok",
            confidence=conf,
            retry_strategy=(
                RetryStrategyName.GRAMMAR.value
                if status == ReviewStatus.REJECT
                else RetryStrategyName.NONE.value
            ),
            t0=t0,
            metadata={"index": index},
        )
