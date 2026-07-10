"""Progress heartbeats for long-running AI Core stages (orchestrator, streaming)."""

from __future__ import annotations

import time
from typing import Any


def emit_ai_core_heartbeat(task_id: str, **fields: Any) -> None:
    """Keep pipeline watchdog alive during blocking AI Core work."""
    if not task_id:
        return
    payload = dict(fields)
    payload.setdefault("phase", "ai_core")
    payload["last_heartbeat_at"] = time.time()
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(str(task_id))
            if task:
                detail = task.setdefault("info", {}).setdefault("progress_detail", {})
                detail.update({k: v for k, v in payload.items() if v is not None})
    except Exception:
        pass
    try:
        from engines.pipeline_watchdog import watchdog_heartbeat

        watchdog_heartbeat(str(task_id), **payload)
    except Exception:
        pass


def orchestrator_timeout_scale() -> float:
    """Scale agent wall-clock budgets on CPU-only hosts."""
    try:
        from engines.translation_adapt import _is_cpu_only

        return 2.5 if _is_cpu_only() else 1.0
    except Exception:
        return 1.0
