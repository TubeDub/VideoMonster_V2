"""Entity Reviewer."""

from __future__ import annotations

import time
from typing import Any

from engines.tqe.models import ConfidenceMetrics, ReviewStatus, RetryStrategyName
from engines.tqe.reviewers.base import BaseReviewer
from engines.tqe.rules._registry import load_all_rules


class EntityReviewer(BaseReviewer):
    name = "EntityReviewer"

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
        fn = rules.get("entity")
        errors = list(fn(original, translation, dict(context or {})) if fn else [])
        critical = [e for e in errors if e.get("severity") == "critical"]
        major = [e for e in errors if e.get("severity") == "major"]
        status = ReviewStatus.REJECT if critical else (
            ReviewStatus.WARN if major else ReviewStatus.PASS
        )
        # Critical entity loss always rejects
        if any(e.get("code") in ("entity_missing",) for e in errors):
            status = ReviewStatus.REJECT
        preserved = 1.0
        if errors:
            preserved = max(0.0, 1.0 - 0.25 * len(errors))
        conf = ConfidenceMetrics(entity_preservation=preserved)
        return self._timed(
            status=status,
            errors=errors,
            explanation="; ".join(
                f"{e.get('code')}:{e.get('token') or e.get('detail') or ''}" for e in errors
            )
            or "entities_ok",
            confidence=conf,
            retry_strategy=(
                RetryStrategyName.ENTITY_CORRECTION.value
                if status == ReviewStatus.REJECT
                else RetryStrategyName.NONE.value
            ),
            t0=t0,
            metadata={"index": index},
        )
