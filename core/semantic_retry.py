"""Semantic Retry Manager — strategy from Production TZ §15.

Attempt chain:
  1) normal translation
  2) stricter prompt
  3) alternate model
  4) manual review flag

Does not auto-apply irreversible architecture changes; only retries LLM text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("tubedub.semantic_retry")

STRICT_SYSTEM = (
    "You are a professional film-dubbing editor.\n"
    "STRICT RULES:\n"
    "1. Preserve EVERY fact, name, number, date, brand, emotion, and cause→effect.\n"
    "2. Do not invent content. Do not drop named entities.\n"
    "3. Output ONE natural target-language line only — no quotes, no notes.\n"
    "4. Prefer shorter spoken phrasing but never truncate meaning.\n"
)


@dataclass
class RetryAttempt:
    attempt: int
    strategy: str
    model: str = ""
    ok: bool = False
    text: str = ""
    issues: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "strategy": self.strategy,
            "model": self.model,
            "ok": self.ok,
            "text": self.text,
            "issues": self.issues,
            "score": self.score,
        }


@dataclass
class RetryResult:
    text: str
    ok: bool
    needs_manual_review: bool = False
    attempts: list[RetryAttempt] = field(default_factory=list)
    final_strategy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "ok": self.ok,
            "needs_manual_review": self.needs_manual_review,
            "final_strategy": self.final_strategy,
            "attempts": [a.to_dict() for a in self.attempts],
        }


def _validate(original: str, text: str, tgt_lang: str) -> tuple[bool, list[str], float]:
    issues: list[str] = []
    score = 0.0
    try:
        from engines.language_intelligence.semantic_validator import (
            validate_semantic_preserve,
        )

        ok, failures = validate_semantic_preserve(original, text, text)
        issues = [
            str(f.get("code") or f) if isinstance(f, dict) else str(f)
            for f in (failures or [])
        ]
        score = 1.0 if ok else max(0.0, 1.0 - 0.2 * len(issues))
        if not ok:
            return False, issues, score
    except Exception:
        pass
    try:
        from engines.quality_score_v2 import compute_quality_score_v2

        score, det = compute_quality_score_v2(
            original, text, tgt_lang=tgt_lang
        )
        dims = det.get("dimensions") or {}
        if dims.get("entity_preservation", 1) < 0.5:
            issues.append("entity_loss")
        if dims.get("hallucination_detection", 1) < 0.5:
            issues.append("hallucination")
        if dims.get("semantic_similarity", 1) < 0.45:
            issues.append("meaning_loss")
        ok = score >= 55.0 and not issues
        return ok, issues, score
    except Exception as exc:
        logger.debug("semantic retry validate fallback: %s", exc)
        return bool(text.strip()), ([] if text.strip() else ["empty"]), 0.0


def run_semantic_retry(
    original: str,
    *,
    tgt_lang: str = "uk",
    translate_fn: Callable[..., str] | None = None,
    models: list[str] | None = None,
    max_attempts: int = 4,
) -> RetryResult:
    """Run up to 4 strategies. ``translate_fn(prompt, *, system, model) -> text``."""
    if not translate_fn:
        return RetryResult(text="", ok=False, needs_manual_review=True, final_strategy="no_fn")

    model_list = list(models or []) or [""]
    attempts: list[RetryAttempt] = []
    best_text = ""
    best_score = -1.0

    strategies = [
        ("normal", "Translate faithfully for film dubbing. Keep names and facts."),
        ("strict", STRICT_SYSTEM),
        ("alt_model", STRICT_SYSTEM),
        ("manual", ""),
    ]

    for i, (name, system) in enumerate(strategies[: max(1, max_attempts)], start=1):
        if name == "manual":
            attempts.append(
                RetryAttempt(
                    attempt=i,
                    strategy="manual_review",
                    ok=False,
                    text=best_text,
                    issues=["needs_manual_review"],
                    score=best_score,
                )
            )
            return RetryResult(
                text=best_text,
                ok=False,
                needs_manual_review=True,
                attempts=attempts,
                final_strategy="manual_review",
            )

        model = model_list[0]
        if name == "alt_model" and len(model_list) > 1:
            model = model_list[1]

        try:
            text = translate_fn(
                original,
                system=system,
                model=model,
                strict=(name != "normal"),
            )
        except Exception as exc:
            attempts.append(
                RetryAttempt(
                    attempt=i,
                    strategy=name,
                    model=model or "",
                    ok=False,
                    issues=[str(exc)],
                )
            )
            continue

        text = str(text or "").strip()
        ok, issues, score = _validate(original, text, tgt_lang)
        attempts.append(
            RetryAttempt(
                attempt=i,
                strategy=name,
                model=model or "",
                ok=ok,
                text=text,
                issues=issues,
                score=score,
            )
        )
        if score > best_score and text:
            best_score = score
            best_text = text
        if ok and text:
            return RetryResult(
                text=text,
                ok=True,
                needs_manual_review=False,
                attempts=attempts,
                final_strategy=name,
            )

    return RetryResult(
        text=best_text,
        ok=False,
        needs_manual_review=True,
        attempts=attempts,
        final_strategy="exhausted",
    )
