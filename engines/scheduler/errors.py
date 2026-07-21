"""Scheduler errors — Freeze TZ P1."""

from __future__ import annotations

from typing import Any


class SchedulerError(Exception):
    """Timing mutation rejected by Scheduler."""

    code: str = "scheduler_error"

    def __init__(
        self,
        message: str,
        *,
        segment_id: str = "",
        field: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.segment_id = segment_id
        self.field = field
        self.details = details or {}
