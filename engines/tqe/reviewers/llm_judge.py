"""LLM Judge — last stage only; never used as translator."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from engines.tqe.models import ConfidenceMetrics, ReviewStatus, RetryStrategyName
from engines.tqe.reviewers.base import BaseReviewer

logger = logging.getLogger("tubedub.tqe.llm_judge")


class LLMJudgeReviewer(BaseReviewer):
    name = "LLMJudgeReviewer"

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
        prior_errors = list(ctx.get("prior_critical_errors") or [])
        # TZ: if prior reviewers already found critical errors — do not call LLM
        if prior_errors:
            return self._timed(
                status=ReviewStatus.SKIP,
                errors=[],
                explanation="skipped_due_to_prior_critical",
                confidence=ConfidenceMetrics(),
                llm_used=False,
                t0=t0,
                metadata={"index": index, "skipped": True},
            )

        if os.getenv("TQE_LLM_JUDGE", "").strip().lower() not in ("1", "true", "yes", "on"):
            return self._timed(
                status=ReviewStatus.SKIP,
                errors=[],
                explanation="llm_judge_disabled",
                confidence=ConfidenceMetrics(),
                llm_used=False,
                t0=t0,
                metadata={"index": index, "disabled": True},
            )

        prompt = (
            "You are a translation quality expert (not a translator).\n"
            "Return ONLY JSON: {\"verdict\":\"PASS\"|\"FAIL\",\"reasons\":[...],\"recommendations\":[...]}\n"
            f"Original: {original}\n"
            f"Translation: {translation}\n"
            f"Error list: {json.dumps(ctx.get('prior_errors') or [], ensure_ascii=False)}\n"
        )
        try:
            from engines.llm_adaptation_mode import chat_completion

            raw = chat_completion(
                prompt,
                system="You judge translation quality. Never rewrite the translation.",
                temperature=0.0,
                max_tokens=300,
            )
            text = str(raw or "").strip()
            data = {}
            try:
                data = json.loads(text[text.find("{") : text.rfind("}") + 1])
            except Exception:
                data = {"verdict": "PASS" if "PASS" in text.upper() else "FAIL", "reasons": [text[:200]]}
            verdict = str(data.get("verdict") or "PASS").upper()
            reasons = list(data.get("reasons") or [])
            status = ReviewStatus.REJECT if verdict == "FAIL" else ReviewStatus.PASS
            errors = [
                {"code": "llm_judge_fail", "detail": r, "severity": "critical"}
                for r in reasons
            ] if status == ReviewStatus.REJECT else []
            return self._timed(
                status=status,
                errors=errors,
                explanation="; ".join(str(r) for r in reasons) or verdict,
                confidence=ConfidenceMetrics(
                    meaning_coverage=0.3 if status == ReviewStatus.REJECT else 1.0
                ),
                retry_strategy=(
                    RetryStrategyName.MEANING_PRESERVATION.value
                    if status == ReviewStatus.REJECT
                    else RetryStrategyName.NONE.value
                ),
                llm_used=True,
                t0=t0,
                metadata={
                    "index": index,
                    "recommendations": list(data.get("recommendations") or []),
                },
            )
        except Exception as exc:
            logger.debug("LLM judge skipped: %s", exc)
            return self._timed(
                status=ReviewStatus.SKIP,
                errors=[],
                explanation=f"llm_unavailable:{exc}",
                confidence=ConfidenceMetrics(),
                llm_used=False,
                fallback_used=True,
                t0=t0,
                metadata={"index": index},
            )
