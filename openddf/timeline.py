"""
Open Developer Diagnostic Framework (OpenDDF) v0.1.0 — Timeline tracker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class TimelineTracker:
    """Chronological event log for diagnostic sessions."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def add_event(
        self,
        event_name: str,
        status: str = "OK",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._events.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_name": event_name,
                "status": status,
                "metadata": dict(metadata or {}),
            }
        )

    def get_events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def export_text(self) -> str:
        lines: list[str] = []
        for ev in self._events:
            ts = ev.get("timestamp", "")
            name = ev.get("event_name", "?")
            status = ev.get("status", "OK")
            meta = ev.get("metadata") or {}
            lines.append(f"[{ts}] {name} -> Status: {status} | Meta: {meta}")
        return "\n".join(lines)
