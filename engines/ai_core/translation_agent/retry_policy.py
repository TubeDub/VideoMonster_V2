"""Retry and fallback policy for Translation Agent v1.0."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from engines.ai_core.translation_agent.confidence import (
    SegmentConfidence,
    translation_confidence,
)
from engines.ai_core.translation_agent.translator_interface import (
    BaseTranslator,
    TranslatorRegistry,
)

logger = logging.getLogger("tubedub.translation_agent.retry")

DEFAULT_CONFIDENCE_THRESHOLD = 0.7
MAX_RETRIES = 3
MAX_ATTEMPTS = 3


@dataclass
class TranslateAttemptResult:
    translated: str
    translator_name: str
    success: bool
    attempt: int
    confidence: float
    error: str | None = None
    fallback_used: bool = False
    decision_log: list[str] = field(default_factory=list)


def translate_with_fallback(
    text: str,
    source: str,
    target: str,
    registry: TranslatorRegistry,
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    max_retries: int = MAX_RETRIES,
) -> TranslateAttemptResult:
    """Try translators in fallback chain; retry when confidence is below threshold."""
    chain = registry.fallback_chain()
    decision_log: list[str] = []
    last_error: str | None = None
    fallback_used = False
    attempt = 0

    if not chain:
        decision_log.append("no_translator_available")
        return TranslateAttemptResult(
            translated="",
            translator_name="none",
            success=False,
            attempt=0,
            confidence=0.0,
            error="no_translator_available",
            decision_log=decision_log,
        )

    for chain_idx, translator in enumerate(chain):
        if chain_idx > 0:
            fallback_used = True
            decision_log.append(f"fallback_to={translator.name}")

        for retry in range(max_retries):
            try:
                translated = translator.translate(text, source, target)
                conf = translation_confidence(
                    translator_name=translator.name,
                    success=True,
                    attempt=retry + 1,
                    source=text,
                    translated=translated,
                )
                attempt = retry + 1 + chain_idx * max_retries
                decision_log.append(
                    f"attempt={attempt} translator={translator.name} confidence={conf}"
                )
                if conf >= threshold:
                    return TranslateAttemptResult(
                        translated=translated,
                        translator_name=translator.name,
                        success=True,
                        attempt=attempt,
                        confidence=conf,
                        fallback_used=fallback_used,
                        decision_log=decision_log,
                    )
                decision_log.append(f"low_confidence={conf} retry={retry + 1}")
                last_error = f"low_confidence:{conf}"
            except Exception as exc:
                last_error = str(exc)
                attempt = retry + 1 + chain_idx * max_retries
                decision_log.append(
                    f"attempt={attempt} translator={translator.name} error={exc}"
                )
                logger.debug("Translate attempt failed: %s", exc)

    # Last resort: rule-based MT with detected source language — never pass source through.
    decision_log.append("exhausted_fallback_chain_try_rule_mt")
    try:
        from engines.pipeline_language_gate import (
            detect_segment_language,
            is_critical_language_mismatch,
        )

        detected = detect_segment_language(str(text or ""), target_lang=target)
        effective_src = detected if detected not in ("empty", "unknown") else source
        if effective_src == target:
            effective_src = "en"
        for translator in chain:
            if translator.name != "deep-translator":
                continue
            try:
                translated = translator.translate(text, effective_src, target)
                translated = str(translated or "").strip()
                bad, _ = is_critical_language_mismatch(
                    translated,
                    target_lang=target,
                    original=str(text or ""),
                )
                if translated and not bad and translated != str(text or "").strip():
                    decision_log.append(
                        f"rule_fallback_ok translator={translator.name} src={effective_src}"
                    )
                    return TranslateAttemptResult(
                        translated=translated,
                        translator_name=translator.name,
                        success=True,
                        attempt=attempt + 1,
                        confidence=0.55,
                        fallback_used=True,
                        decision_log=decision_log,
                    )
            except Exception as exc:
                decision_log.append(f"rule_fallback_error={exc}")
                last_error = str(exc)
    except Exception as exc:
        decision_log.append(f"rule_fallback_setup_error={exc}")
        last_error = str(exc)

    decision_log.append("exhausted_fallback_chain_empty_result")
    return TranslateAttemptResult(
        translated="",
        translator_name=chain[-1].name if chain else "none",
        success=False,
        attempt=attempt,
        confidence=0.0,
        error=last_error or "exhausted_fallback_chain",
        fallback_used=True,
        decision_log=decision_log,
    )
