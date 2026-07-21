"""P707 Platform Event Bus — plugins subscribe safely."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Callable

from engines.platform_sdk.types import PlatformEvent

Listener = Callable[[str, dict[str, Any]], None]

_LOCK = threading.RLock()
_BUS: "PlatformEventBus" | None = None


class PlatformEventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._history: list[dict[str, Any]] = []
        self._max_history = 1000

    def subscribe(self, event: PlatformEvent | str, listener: Listener) -> None:
        key = event.value if isinstance(event, PlatformEvent) else str(event)
        with _LOCK:
            self._listeners[key].append(listener)

    def unsubscribe(self, event: PlatformEvent | str, listener: Listener) -> None:
        key = event.value if isinstance(event, PlatformEvent) else str(event)
        with _LOCK:
            lst = self._listeners.get(key) or []
            self._listeners[key] = [x for x in lst if x is not listener]

    def publish(self, event: PlatformEvent | str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        key = event.value if isinstance(event, PlatformEvent) else str(event)
        record = {
            "event": key,
            "ts": time.time(),
            "payload": dict(payload or {}),
        }
        with _LOCK:
            self._history.append(record)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]
            listeners = list(self._listeners.get(key, [])) + list(self._listeners.get("*", []))
        for fn in listeners:
            try:
                fn(key, record)
            except Exception:
                pass
        # Bridge to engines.core EventBus (best-effort, no core mutation)
        try:
            from engines.core.events import get_event_bus

            get_event_bus().emit(key, record["payload"])
        except Exception:
            pass
        return record

    def history(self, *, event: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with _LOCK:
            items = self._history
            if event:
                items = [e for e in items if e["event"] == event]
            return list(items[-limit:])


def get_platform_bus() -> PlatformEventBus:
    global _BUS
    with _LOCK:
        if _BUS is None:
            _BUS = PlatformEventBus()
        return _BUS
