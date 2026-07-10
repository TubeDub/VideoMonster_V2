"""Simple in-process event bus for pipeline stages."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Callable

Listener = Callable[[str, dict[str, Any]], None]

_LOCK = threading.RLock()
_BUS: EventBus | None = None


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._history: list[dict[str, Any]] = []
        self._max_history = 500

    def subscribe(self, stage: str, listener: Listener) -> None:
        with _LOCK:
            self._listeners[stage].append(listener)

    def emit(self, stage: str, payload: dict[str, Any] | None = None) -> None:
        event = {
            "stage": stage,
            "ts_ms": int(time.time() * 1000),
            "payload": dict(payload or {}),
        }
        with _LOCK:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            listeners = list(self._listeners.get(stage, [])) + list(self._listeners.get("*", []))
        for fn in listeners:
            try:
                fn(stage, event)
            except Exception:
                pass

    def history(self, *, stage: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with _LOCK:
            items = self._history
            if stage:
                items = [e for e in items if e["stage"] == stage]
            return list(items[-limit:])


def get_event_bus() -> EventBus:
    global _BUS
    with _LOCK:
        if _BUS is None:
            _BUS = EventBus()
        return _BUS
