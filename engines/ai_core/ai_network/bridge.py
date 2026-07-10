"""AI Network bridge — helpers for agents, streaming, coordinator (TZ #1 §3)."""

from __future__ import annotations

import logging
from typing import Any

from engines.ai_core.ai_event_log import agent_finished, agent_started, log_ai_event
from engines.ai_core.ai_network.bus import get_network
from engines.ai_core.ai_network.envelope import (
    EVENT_AGENT_FINISHED,
    EVENT_AGENT_STARTED,
    EVENT_RECOVERY_ACTION,
    EVENT_SEGMENT_IN,
    EVENT_SEGMENT_OUT,
    EVENT_SKILL_VIOLATION,
)

logger = logging.getLogger("tubedub.ai_core.ai_network.bridge")


def _model_hint() -> str:
    try:
        from engines.llm_adaptation_mode import detect_capabilities

        return str(detect_capabilities().get("model") or "")
    except Exception:
        return ""


def emit_agent_started(
    run_id: str,
    agent: str,
    *,
    segment_index: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    model = _model_hint()
    agent_started(run_id, agent, model=model)
    payload: dict[str, Any] = {"agent": agent, "model": model}
    if segment_index is not None:
        payload["segment_index"] = segment_index
    if extra:
        payload.update(extra)
    get_network(run_id).publish(EVENT_AGENT_STARTED, agent, payload)


def emit_agent_finished(
    run_id: str,
    agent: str,
    *,
    status: str = "success",
    ms: float | None = None,
    segment_index: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    model = _model_hint()
    agent_finished(run_id, agent, status=status, ms=ms, model=model)
    payload: dict[str, Any] = {"agent": agent, "status": status, "model": model}
    if ms is not None:
        payload["ms"] = ms
    if segment_index is not None:
        payload["segment_index"] = segment_index
    if extra:
        payload.update(extra)
    get_network(run_id).publish(EVENT_AGENT_FINISHED, agent, payload)


def emit_segment_in(
    run_id: str,
    agent: str,
    segment_index: int,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"agent": agent, "segment_index": segment_index}
    if extra:
        payload.update(extra)
    get_network(run_id).publish(EVENT_SEGMENT_IN, agent, payload)
    log_ai_event(run_id, "SegmentIn", agent=agent, extra={"segment_index": segment_index})


def emit_segment_out(
    run_id: str,
    agent: str,
    segment_index: int,
    *,
    status: str = "success",
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "agent": agent,
        "segment_index": segment_index,
        "status": status,
    }
    if extra:
        payload.update(extra)
    get_network(run_id).publish(EVENT_SEGMENT_OUT, agent, payload)
    log_ai_event(
        run_id,
        "SegmentOut",
        agent=agent,
        status=status,
        extra={"segment_index": segment_index},
    )


def emit_recovery_action(
    run_id: str,
    *,
    from_agent: str,
    to_agent: str,
    segment_index: int,
    reason: str = "",
) -> None:
    payload = {
        "from_agent": from_agent,
        "to_agent": to_agent,
        "segment_index": segment_index,
        "reason": reason,
    }
    get_network(run_id).publish(EVENT_RECOVERY_ACTION, from_agent, payload)
    log_ai_event(
        run_id,
        "RecoveryAction",
        agent=from_agent,
        extra=payload,
    )


def emit_skill_violation(run_id: str, violation: dict[str, Any]) -> None:
    get_network(run_id).publish(EVENT_SKILL_VIOLATION, "global_skill", violation)
