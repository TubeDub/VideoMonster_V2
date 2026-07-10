"""Storage events (Storage Manager §7).

Легковесная шина событий хранилища: подписка/публикация в процессе плюс
опциональный журнал JSONL на диск. Не заменяет AI Network / pipeline EventBus —
описывает исключительно жизненный цикл проектов и сессий.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.storage.events")


class StorageEvent:
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_OPENED = "PROJECT_OPENED"
    PROJECT_SAVED = "PROJECT_SAVED"
    PROJECT_CLOSED = "PROJECT_CLOSED"
    PROJECT_REMOVED = "PROJECT_REMOVED"
    PROJECT_TRASHED = "PROJECT_TRASHED"
    PROJECT_RESTORED = "PROJECT_RESTORED"
    PROJECT_DELETED = "PROJECT_DELETED"
    PROJECT_IMPORTED = "PROJECT_IMPORTED"
    PROJECT_EXPORTED = "PROJECT_EXPORTED"
    PROJECT_MIGRATED = "PROJECT_MIGRATED"
    TRASH_EMPTIED = "TRASH_EMPTIED"
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_FINISHED = "SESSION_FINISHED"
    STORAGE_CLEANUP = "STORAGE_CLEANUP"


ALL_EVENTS = frozenset(
    v
    for k, v in vars(StorageEvent).items()
    if not k.startswith("_") and isinstance(v, str)
)

Handler = Callable[[str, dict[str, Any]], None]


class StorageEventBus:
    """Thread-safe subscribe/publish bus with optional JSONL journalling."""

    def __init__(self, journal_path: Path | None = None):
        self._subscribers: dict[str, list[Handler]] = {}
        self._wildcard: list[Handler] = []
        self._lock = threading.Lock()
        self.journal_path = Path(journal_path) if journal_path else None

    def subscribe(self, event_type: str, handler: Handler) -> None:
        with self._lock:
            if event_type == "*":
                self._wildcard.append(handler)
            else:
                self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        with self._lock:
            if event_type == "*":
                if handler in self._wildcard:
                    self._wildcard.remove(handler)
            else:
                handlers = self._subscribers.get(event_type) or []
                if handler in handlers:
                    handlers.remove(handler)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "event": event_type,
            "ts": time.time(),
            "payload": dict(payload or {}),
        }
        with self._lock:
            handlers = list(self._subscribers.get(event_type) or [])
            handlers.extend(self._wildcard)
        for handler in handlers:
            try:
                handler(event_type, event["payload"])
            except Exception as exc:
                logger.debug("storage event handler failed (%s): %s", event_type, exc)
        self._journal(event)
        return event

    def _journal(self, event: dict[str, Any]) -> None:
        if not self.journal_path:
            return
        try:
            import json

            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.journal_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("storage event journal write failed: %s", exc)
