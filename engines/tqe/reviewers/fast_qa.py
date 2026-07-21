"""Fast QA — first layer, no LLM."""

from __future__ import annotations

import re
import time
from typing import Any

from engines.tqe.models import ConfidenceMetrics, ReviewStatus, RetryStrategyName
from engines.tqe.reviewers.base import BaseReviewer
from engines.tqe.rules._registry import load_all_rules


class FastQAReviewer(BaseReviewer):
    name = "FastQAReviewer"

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
        text = (translation or "").strip()
        src = (original or "").strip()
        errors: list[dict] = []

        if not text:
            errors.append({"code": "empty_translation", "severity": "critical"})
        elif src and len(text) < max(3, int(len(src) * 0.08)):
            errors.append({"code": "too_short", "severity": "critical"})
        elif src and len(text) > max(80, int(len(src) * 3.5)):
            errors.append({"code": "too_long", "severity": "major"})

        if text and re.search(r"(.)\1{8,}", text):
            errors.append({"code": "garbage_repeat", "severity": "critical"})

        rules = load_all_rules()
        for rule_name in ("hallucination", "numbers", "dates", "quotes", "sentence"):
            fn = rules.get(rule_name)
            if not fn:
                continue
            for err in fn(src, text, ctx) or []:
                errors.append(err)

        critical = [e for e in errors if e.get("severity") == "critical"]
        status = ReviewStatus.REJECT if critical or any(
            e.get("code") in ("empty_translation", "too_short") for e in errors
        ) else (ReviewStatus.WARN if errors else ReviewStatus.PASS)

        conf = ConfidenceMetrics(
            sentence_completeness=0.0 if any(e.get("code") == "incomplete_sentence" for e in errors) else 1.0,
            grammar_integrity=0.2 if critical else (0.7 if errors else 1.0),
        )
        strategy = (
            RetryStrategyName.COMPLETION.value
            if status == ReviewStatus.REJECT
            else RetryStrategyName.NONE.value
        )
        return self._timed(
            status=status,
            errors=errors,
            explanation="; ".join(e.get("code", "") for e in errors) or "fast_qa_ok",
            metrics={"error_count": len(errors)},
            confidence=conf,
            retry_strategy=strategy,
            t0=t0,
            metadata={"index": index},
        )
