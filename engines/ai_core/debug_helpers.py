"""Shared helpers for Debug/Learning mode across AI Core agents."""

from __future__ import annotations

from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE


def is_debug_learning_mode() -> bool:
    return IS_DEBUG_LEARNING_MODE()


def finalize_agent_status(status: str, *, debug_mode: bool | None = None) -> str:
    """Downgrade agent error status to warning in debug/learning mode."""
    dm = is_debug_learning_mode() if debug_mode is None else debug_mode
    if dm and status == "error":
        return "warning"
    return status


def gatekeeper_allows(
    gate_ok: bool,
    gate_msgs: list[str],
    errors: list[str],
    warnings: list[str],
    *,
    debug_mode: bool | None = None,
) -> bool:
    """Return True when gate passes or debug mode bypasses gatekeeper."""
    dm = is_debug_learning_mode() if debug_mode is None else debug_mode
    if gate_ok:
        warnings.extend(gate_msgs)
        return True
    if dm:
        warnings.extend(gate_msgs)
        warnings.append("gatekeeper_bypassed_debug_mode")
        return True
    errors.extend(gate_msgs)
    return False


def record_llm_skipped(task_id: str, agent_name: str) -> None:
    """Record LLM timeout/unavailability in OpenDDF without stopping pipeline."""
    try:
        from engines.open_ddf import open_ddf

        open_ddf.record_agent(
            task_id,
            agent_name,
            called=True,
            success=True,
            decision="LLM skipped",
            fallback_used=True,
        )
    except Exception:
        pass


__all__ = [
    "is_debug_learning_mode",
    "finalize_agent_status",
    "gatekeeper_allows",
    "record_llm_skipped",
]
