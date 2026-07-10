"""AI Core — LLM Gateway.

THE single entry point for every LLM chat call in TubeDub. No module may talk
to a language model directly; they all go through ``ai_core.llm_gateway`` (which
delegates to the audited, cached, per-segment-budgeted transport in
``engines.translation_adapt``). This is what makes "all LLM calls pass through
AI Core" (ТЗ / DoD) enforceable and observable.

Responsibilities:
  • decide availability (is an LLM usable at all);
  • own the per-segment budget context (begin_run / set_context);
  • perform the actual chat call with caching + call logging;
  • never let a raw provider HTTP request escape a module;
  • ``can_call_llm`` — pre-flight guard; returns (False, reason) instead of raising.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("tubedub.ai_core.llm_gateway")

RULE_FALLBACK_REASONS = frozenset({
    "no_endpoint",
    "llm_circuit_open",
    "segment_time_budget",
    "segment_breaker_open",
    "budget_exhausted",
    "llm_semaphore_timeout",
    "disabled",
    "error",
})

_CIRCUIT_COOLDOWN_S = float(os.getenv("VM_LLM_CIRCUIT_COOLDOWN_S", "45"))
_circuit_opened_at: float | None = None
_llm_decisions: list[dict] = []


def _cooldown_seconds() -> float:
    return max(5.0, _CIRCUIT_COOLDOWN_S)


def reset_circuit_for_phase(phase: str) -> None:
    """Isolate circuit breaker per pipeline phase (e.g. POST_TTS_QA)."""
    global _circuit_opened_at
    _circuit_opened_at = None
    try:
        from engines.translation_adapt import reset_circuit_for_phase as _reset_phase

        _reset_phase(phase)
    except Exception:
        logger.debug("reset_circuit_for_phase failed phase=%s", phase, exc_info=True)


def maybe_reset_circuit_after_cooldown() -> bool:
    """Reset global circuit breaker after cooldown elapses. Returns True if reset."""
    global _circuit_opened_at
    try:
        from engines.translation_adapt import circuit_open, reset_circuit_breaker

        if not circuit_open():
            _circuit_opened_at = None
            return False
        if _circuit_opened_at is None:
            _circuit_opened_at = time.monotonic()
            return False
        if time.monotonic() - _circuit_opened_at >= _cooldown_seconds():
            reset_circuit_breaker()
            _circuit_opened_at = None
            logger.info(
                "[LLM Gateway] circuit breaker reset after %.0fs cooldown",
                _cooldown_seconds(),
            )
            return True
    except Exception:
        logger.debug("circuit cooldown check failed", exc_info=True)
    return False


def _mark_circuit_opened() -> None:
    global _circuit_opened_at
    try:
        from engines.translation_adapt import circuit_open

        if circuit_open() and _circuit_opened_at is None:
            _circuit_opened_at = time.monotonic()
    except Exception:
        pass


def _record_llm_decision(
    task_id: str,
    segment_idx: int | None,
    allowed: bool,
    reason: str,
) -> None:
    entry = {
        "task_id": task_id,
        "segment": segment_idx,
        "allowed": allowed,
        "reason": reason or ("ok" if allowed else "blocked"),
        "ts": time.time(),
    }
    _llm_decisions.append(entry)
    if len(_llm_decisions) > 5000:
        del _llm_decisions[:2500]
    try:
        from engines.open_ddf import open_ddf

        if not allowed and reason:
            open_ddf.record_agent(
                task_id,
                "LLM Gateway",
                called=False,
                success=True,
                decision=f"LLM skipped: {reason}",
                fallback_used=True,
                segment_idx=segment_idx,
            )
    except Exception:
        logger.debug("OpenDDF llm decision record failed", exc_info=True)


def can_call_llm(task_id: str, segment_idx: int | None) -> tuple[bool, str]:
    """Pre-flight LLM availability for a segment.

    Returns (True, "") when a call may proceed, else (False, reason).
    Never raises — callers must use rule-based fallback when False.
    """
    maybe_reset_circuit_after_cooldown()
    try:
        from engines.translation_adapt import (
            _segment_breaker_open,
            _segment_time_budget_open,
            circuit_open,
            llm_rephrase_available,
        )
    except Exception:
        _record_llm_decision(task_id, segment_idx, False, "transport_unavailable")
        return False, "transport_unavailable"

    if not llm_rephrase_available():
        _record_llm_decision(task_id, segment_idx, False, "no_endpoint")
        return False, "no_endpoint"

    if circuit_open():
        _mark_circuit_opened()
        _record_llm_decision(task_id, segment_idx, False, "llm_circuit_open")
        return False, "llm_circuit_open"

    if _segment_time_budget_open(segment_idx):
        _record_llm_decision(task_id, segment_idx, False, "segment_time_budget")
        return False, "segment_time_budget"

    if _segment_breaker_open(segment_idx):
        _record_llm_decision(task_id, segment_idx, False, "segment_breaker_open")
        return False, "segment_breaker_open"

    _record_llm_decision(task_id, segment_idx, True, "")
    return True, ""


def is_available() -> bool:
    """True when an LLM endpoint (local or cloud) is usable AND the global
    circuit breaker is closed.

    The breaker check is what makes legacy callers (e.g. the Adaptive Dubbing
    Adapter's ``_llm_rephrase_variants``) stop hitting a hopeless local model
    for the rest of a run instead of waiting on it segment after segment (P0
    no-hang).
    """
    maybe_reset_circuit_after_cooldown()
    try:
        from engines.translation_adapt import circuit_open, llm_rephrase_available

        return bool(llm_rephrase_available()) and not circuit_open()
    except Exception:
        return False


def begin_run(
    task_id: str | None,
    *,
    mode: str | None = None,
    per_segment_s: float | None = None,
    project_s: float | None = None,
) -> None:
    """Start/continue LLM adaptation for a dub run with an optional budget.

    Thin pass-through to the transport so AI Core owns the budget lifecycle.
    """
    global _circuit_opened_at
    _circuit_opened_at = None
    _llm_decisions.clear()
    try:
        from engines.translation_adapt import begin_llm_run

        begin_llm_run(
            task_id, mode=mode, per_segment_s=per_segment_s, project_s=project_s
        )
    except Exception:
        logger.debug("begin_run: transport unavailable", exc_info=True)


def set_context(*, segment: int | None = None, stage: str = "") -> None:
    """Attribute subsequent LLM calls to a segment/stage (audit)."""
    try:
        from engines.translation_adapt import set_llm_context

        set_llm_context(segment=segment, stage=stage)
    except Exception:
        logger.debug("set_context: transport unavailable", exc_info=True)


def chat(
    prompt: str,
    *,
    task_id: str = "",
    segment_idx: int | None = None,
    system: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float | None = None,
    count_budget: bool = True,
) -> str | None:
    """Single chat completion through the audited transport. Returns text/None.

    When ``count_budget`` is True, ``can_call_llm`` is consulted first; on
    block returns None + records skip reason (never raises).
    """
    if not task_id:
        try:
            from engines.translation_adapt import llm_budget_status

            task_id = str(llm_budget_status().get("task") or "")
        except Exception:
            pass

    if count_budget:
        allowed, reason = can_call_llm(task_id, segment_idx)
        if not allowed:
            try:
                from engines.translation_adapt import record_llm_skip

                record_llm_skip(reason or "blocked")
            except Exception:
                pass
            return None

    try:
        from engines.ai_core.global_skill import augment_system_prompt

        system = augment_system_prompt(system)
    except Exception:
        pass

    try:
        from engines.translation_adapt import _llm_chat, circuit_open

        out = _llm_chat(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            count_budget=count_budget,
            system=system,
        )
        if count_budget and circuit_open():
            _mark_circuit_opened()
        return out
    except Exception:
        logger.debug("chat: transport error", exc_info=True)
        return None


def status() -> dict:
    """Current budget / mode telemetry for reporting."""
    maybe_reset_circuit_after_cooldown()
    try:
        from engines.translation_adapt import llm_budget_status

        st = dict(llm_budget_status())
        st["circuit_cooldown_s"] = _cooldown_seconds()
        st["recent_decisions"] = list(_llm_decisions[-20:])
        return st
    except Exception:
        return {}


def calls() -> list:
    """All recorded LLM calls for this run (for the AI Core report)."""
    try:
        from engines.translation_adapt import get_llm_calls

        return list(get_llm_calls())
    except Exception:
        return []


def decisions() -> list[dict]:
    """Recent can_call_llm decisions for the AI Core report."""
    return list(_llm_decisions)


def active_model() -> str:
    """The model AI Core would use for the active endpoint."""
    try:
        from engines.translation_adapt import _llm_model

        return str(_llm_model())
    except Exception:
        return ""


__all__ = [
    "RULE_FALLBACK_REASONS",
    "is_available",
    "begin_run",
    "set_context",
    "chat",
    "can_call_llm",
    "maybe_reset_circuit_after_cooldown",
    "reset_circuit_for_phase",
    "status",
    "calls",
    "decisions",
    "active_model",
]
