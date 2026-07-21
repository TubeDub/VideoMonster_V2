"""P616 Retry + P617 Provider Failover."""

from __future__ import annotations

import logging
from typing import Any, Callable

from engines.voice_platform.types import SynthesisRequest, SynthesisResult

logger = logging.getLogger("tubedub.voice_platform.failover")

DEFAULT_FAILOVER_CHAIN = ("edge-offline", "mock")


def decide_retry_strategy(
    error: str,
    *,
    attempt: int,
    max_attempts: int = 3,
) -> str:
    """
    P616 — Decision Layer choices: retry | other_voice | other_engine | manual_review.
    """
    err = (error or "").lower()
    if attempt >= max_attempts:
        if "voice" in err or "speaker" in err:
            return "other_voice"
        return "manual_review"
    if "timeout" in err or "unavailable" in err or "not installed" in err:
        return "other_engine"
    if "rate" in err or "throttle" in err:
        return "retry"
    if attempt == 1:
        return "retry"
    if attempt == 2:
        return "other_engine"
    return "manual_review"


def failover_providers(
    preferred: str | None,
    *,
    chain: tuple[str, ...] | None = None,
) -> list[str]:
    """P617 — ordered provider list without stopping the Pipeline."""
    chain = chain or DEFAULT_FAILOVER_CHAIN
    ordered: list[str] = []
    if preferred:
        ordered.append(preferred)
    for p in chain:
        if p not in ordered:
            ordered.append(p)
    return ordered


def synthesize_with_failover(
    request: SynthesisRequest,
    *,
    synthesize_fn: Callable[..., SynthesisResult],
    chain: tuple[str, ...] | None = None,
    max_attempts: int = 3,
) -> SynthesisResult:
    """Try preferred provider then failover; apply retry strategy."""
    providers = failover_providers(request.provider, chain=chain)
    last = SynthesisResult(ok=False, error="no providers", speech_uuid=request.speech_uuid)
    attempt = 0
    idx = 0
    while attempt < max_attempts and idx < len(providers):
        attempt += 1
        pid = providers[idx]
        req = SynthesisRequest(**{**request.to_dict(), "provider": pid})
        try:
            result = synthesize_fn(req)
        except Exception as exc:
            result = SynthesisResult(ok=False, error=str(exc), provider=pid)
        if result.ok:
            result.meta = dict(result.meta or {})
            result.meta["failover_attempt"] = attempt
            result.meta["provider_used"] = pid
            return result
        last = result
        strategy = decide_retry_strategy(result.error, attempt=attempt, max_attempts=max_attempts)
        logger.warning(
            "VoicePlatform failover: provider=%s attempt=%s strategy=%s err=%s",
            pid,
            attempt,
            strategy,
            result.error,
        )
        if strategy == "retry":
            continue
        if strategy in {"other_engine", "other_voice"}:
            idx += 1
            continue
        # manual_review
        last.meta = dict(last.meta or {})
        last.meta["manual_review"] = True
        return last
    last.meta = dict(last.meta or {})
    last.meta["manual_review"] = True
    return last
