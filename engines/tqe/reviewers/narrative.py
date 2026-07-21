"""Narrative Reviewer — story continuity across the batch context."""

from __future__ import annotations

import time
from typing import Any

from engines.tqe.models import ConfidenceMetrics, ReviewStatus, RetryStrategyName
from engines.tqe.reviewers.base import BaseReviewer


class NarrativeReviewer(BaseReviewer):
    name = "NarrativeReviewer"

    def review(
        self,
        *,
        index: int,
        original: str,
        translation: str,
        context: dict[str, Any] | None = None,
    ):
        t0 = time.perf_counter()
        ctx = dict(context or {})
        errors: list[dict] = []
        src = (original or "").strip()
        tr = (translation or "").strip()
        src_words = len(src.split())
        tr_words = len(tr.split())

        # Large source with tiny translation = narrative drop
        if src_words >= 40 and tr_words < max(10, int(src_words * 0.2)):
            errors.append(
                {
                    "code": "narrative_drop",
                    "severity": "critical",
                    "detail": f"src_words={src_words} tr_words={tr_words}",
                }
            )

        # Batch-level: previous segment ending unfinished + this starts mid-glue
        prev_tr = str(ctx.get("prev_translation") or "").strip()
        if prev_tr and prev_tr[-1] not in ".!?…" and tr.lower().startswith(
            ("між ", "і ", "а ", "що ")
        ):
            # soft warn — may be legitimate continuation
            errors.append(
                {
                    "code": "narrative_join_risk",
                    "severity": "warn",
                }
            )

        critical = [e for e in errors if e.get("severity") == "critical"]
        status = ReviewStatus.REJECT if critical else (
            ReviewStatus.WARN if errors else ReviewStatus.PASS
        )
        conf = ConfidenceMetrics(
            narrative_integrity=0.0 if critical else (0.7 if errors else 1.0)
        )
        return self._timed(
            status=status,
            errors=errors,
            explanation="; ".join(e.get("code", "") for e in errors) or "narrative_ok",
            confidence=conf,
            retry_strategy=(
                RetryStrategyName.NARRATIVE.value
                if status == ReviewStatus.REJECT
                else RetryStrategyName.NONE.value
            ),
            t0=t0,
            metadata={"index": index},
        )
