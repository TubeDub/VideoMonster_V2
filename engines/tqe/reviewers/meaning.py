"""Meaning Reviewer — events / causation / negation."""

from __future__ import annotations

import time
from typing import Any

from engines.tqe.models import ConfidenceMetrics, ReviewStatus, RetryStrategyName
from engines.tqe.reviewers.base import BaseReviewer
from engines.tqe.rules._registry import load_all_rules


class MeaningReviewer(BaseReviewer):
    name = "MeaningReviewer"

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
        fn = rules.get("meaning")
        errors = list(fn(original, translation, dict(context or {})) if fn else [])

        # Optional semantic_meaning gate when available
        try:
            from engines.semantic_meaning import verify_meaning_preserved

            ok, reason, _ = verify_meaning_preserved(original, translation, translation)
            if not ok and reason not in ("ok", "unchanged", "preserved_token"):
                errors.append(
                    {
                        "code": f"semantic:{reason}",
                        "severity": "critical" if reason in (
                            "incomplete_sentence",
                            "meaning_loss",
                            "entity_loss",
                        ) else "major",
                    }
                )
        except Exception:
            pass

        critical = [e for e in errors if e.get("severity") == "critical"]
        status = ReviewStatus.REJECT if critical else (
            ReviewStatus.WARN if errors else ReviewStatus.PASS
        )
        coverage = 1.0 if not errors else max(0.0, 1.0 - 0.2 * len(errors))
        conf = ConfidenceMetrics(meaning_coverage=coverage)
        return self._timed(
            status=status,
            errors=errors,
            explanation="; ".join(e.get("code", "") for e in errors) or "meaning_ok",
            confidence=conf,
            retry_strategy=(
                RetryStrategyName.MEANING_PRESERVATION.value
                if status == ReviewStatus.REJECT
                else RetryStrategyName.NONE.value
            ),
            t0=t0,
            metadata={"index": index},
        )
