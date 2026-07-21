"""Scheduler — authoritative owner of segment timing after TRANSLATION LOCK.

Freeze TZ P1: the only allowed way to mutate start/end/place timing is via
this API. Direct ``segment["start_ms"] = ...`` outside Scheduler is forbidden
and is caught by architecture tests + StageSnapshotGuard (stage=scheduler).
"""

from __future__ import annotations

from engines.scheduler.api import (
    Scheduler,
    get_scheduler,
    request_time,
    update_time,
)
from engines.scheduler.errors import SchedulerError

__all__ = [
    "Scheduler",
    "SchedulerError",
    "get_scheduler",
    "request_time",
    "update_time",
]
