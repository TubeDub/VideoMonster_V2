"""Post-LOCK Dub Engine boundary — Freeze TZ P1.

This package is the architectural Dub Engine surface after TRANSLATION LOCK.
It knows Scheduler + audio/timing helpers only.

Forbidden (enforced by architecture tests):
  ai_core, Qwen, Ollama, Grammar/Semantic agents, Prompt Builder,
  translation_adapt / translation_pipeline.
"""

from __future__ import annotations

from typing import Any

from engines.scheduler import Scheduler, get_scheduler, request_time, update_time


def schedule_segment_slot(
    segments: list[dict[str, Any]],
    segment_id: str,
    *,
    start_ms: int,
    end_ms: int,
    info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a timing slot via Scheduler (never mutate segment dicts directly)."""
    sched = get_scheduler(info)
    return sched.update_time(
        segments,
        segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
    )


def ensure_segment_duration(
    segments: list[dict[str, Any]],
    segment_id: str,
    required_ms: int,
    *,
    info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Request required duration via Scheduler API."""
    sched = get_scheduler(info)
    return sched.request_time(segments, segment_id, required_ms)


__all__ = [
    "Scheduler",
    "ensure_segment_duration",
    "get_scheduler",
    "request_time",
    "schedule_segment_slot",
    "update_time",
]
