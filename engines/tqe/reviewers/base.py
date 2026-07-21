"""Reviewer base — Chain of Responsibility node."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from engines.tqe.models import ConfidenceMetrics, QualityReport, ReviewStatus


class BaseReviewer(ABC):
    name: str = "base"

    @abstractmethod
    def review(
        self,
        *,
        index: int,
        original: str,
        translation: str,
        context: dict[str, Any] | None = None,
    ) -> QualityReport:
        raise NotImplementedError

    def _timed(
        self,
        *,
        status: ReviewStatus,
        errors: list[dict],
        explanation: str,
        metrics: dict | None = None,
        confidence: ConfidenceMetrics | None = None,
        retry_strategy: str = "none",
        llm_used: bool = False,
        fallback_used: bool = False,
        t0: float | None = None,
        metadata: dict | None = None,
    ) -> QualityReport:
        started = t0 if t0 is not None else time.perf_counter()
        conf = confidence or ConfidenceMetrics()
        hist = [conf.overall()]
        return QualityReport(
            reviewer_name=self.name,
            status=status,
            metrics=dict(metrics or {}),
            errors=list(errors),
            explanation=explanation,
            retry_strategy=retry_strategy,
            metadata=dict(metadata or {}),
            review_time_ms=(time.perf_counter() - started) * 1000,
            llm_used=llm_used,
            fallback_used=fallback_used,
            confidence_history=hist,
            confidence=conf,
        )
