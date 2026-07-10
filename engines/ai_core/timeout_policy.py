"""AI Core agent wall-clock timeouts and timeout debug logging."""

from __future__ import annotations

import logging
import time
from typing import Any

from engines.ai_core.pipeline_heartbeat import orchestrator_timeout_scale

logger = logging.getLogger("tubedub.ai_core.timeout")

_TEXT_AGENTS = frozenset(
    {
        "translation",
        "semantic",
        "timing",
        "grammar",
        "quality",
        "streaming_text",
    }
)


def resolve_agent_timeout(
    agent_name: str,
    base_timeout: int,
    state: dict[str, Any],
) -> int:
    """Scale per-agent wall clock by hardware and segment count."""
    scaled = max(int(base_timeout * orchestrator_timeout_scale()), int(base_timeout))
    if agent_name not in _TEXT_AGENTS:
        return scaled

    segs = len(state.get("segments") or state.get("segments_data") or [])
    if segs <= 0:
        return scaled

    per_seg = 2.0
    try:
        from engines.llm_adaptation_mode import _model_param_billions, detect_capabilities

        model = str(detect_capabilities().get("model") or "")
        param_b = _model_param_billions(model)
        if param_b >= 13:
            per_seg = 4.5
        elif param_b >= 7:
            per_seg = 2.8
        elif param_b > 0 and param_b < 5:
            per_seg = 1.2
        elif param_b <= 0:
            per_seg = 0.25
    except Exception:
        pass

    dynamic = int(segs * per_seg + 90)
    return max(scaled, min(dynamic, 7200))


def log_agent_timeout_debug(
    app_dir,
    task_id: str,
    *,
    agent: str,
    timeout_sec: int,
    error: str,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Debug Mode: log orchestrator wall-clock timeout context."""
    from engines.translation_stage_log import log_llm_timeout_debug

    segs = len((state or {}).get("segments") or (state or {}).get("segments_data") or [])
    payload: dict[str, Any] = {
        "source": "ai_core_orchestrator",
        "agent": agent,
        "wall_timeout_sec": int(timeout_sec),
        "error": str(error or ""),
        "segment_count": segs,
    }
    try:
        from engines.translation_adapt import _llm_call_timeout, _resolve_endpoint, get_llm_inflight_snapshot

        ep = _resolve_endpoint()
        payload["api_url"] = str(ep.get("url") or ep.get("base_url") or "")
        payload["provider"] = str(ep.get("provider") or "")
        payload["model"] = str(ep.get("model") or "")
        payload["llm_call_timeout_sec"] = float(_llm_call_timeout())
        inflight = get_llm_inflight_snapshot() or {}
        payload["wait_sec"] = round(
            max(0.0, time.time() - float(inflight.get("started_at") or 0)), 1
        )
        payload["chars_sent"] = int(inflight.get("chars_sent") or 0)
        payload["attempt"] = int(inflight.get("attempt") or 0)
        payload["inflight_segment"] = inflight.get("segment")
    except Exception as exc:
        payload["diag_error"] = str(exc)

    log_llm_timeout_debug(app_dir, task_id, **payload)
    return payload
