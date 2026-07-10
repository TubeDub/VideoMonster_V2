"""Stage retry with exponential backoff — bounded attempts, surfaced errors."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

logger = logging.getLogger("tubedub.pipeline_orchestrator.stage_retry")

T = TypeVar("T")


@dataclass
class RetryResult:
    ok: bool
    value: Any = None
    error: str = ""
    attempts: int = 0
    errors: list[str] = field(default_factory=list)


def run_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    stage: str = "",
    on_retry: Callable[[int, str, float], None] | None = None,
) -> RetryResult:
    """Run *fn* with exponential backoff between failures."""
    errors: list[str] = []
    delay = base_delay_s
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            val = fn()
            return RetryResult(ok=True, value=val, attempts=attempt, errors=errors)
        except Exception as exc:
            msg = str(exc) or exc.__class__.__name__
            errors.append(msg)
            logger.warning(
                "[StageRetry] %s attempt %d/%d failed: %s",
                stage or "stage",
                attempt,
                max_attempts,
                msg,
            )
            if attempt >= max_attempts:
                return RetryResult(
                    ok=False,
                    error=msg,
                    attempts=attempt,
                    errors=errors,
                )
            if on_retry:
                try:
                    on_retry(attempt, msg, delay)
                except Exception:
                    pass
            time.sleep(min(delay, max_delay_s))
            delay = min(delay * 2.0, max_delay_s)
    return RetryResult(ok=False, error="exhausted", attempts=max_attempts, errors=errors)
