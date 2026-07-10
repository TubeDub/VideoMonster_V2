"""Central Event Bus for TubeDub (TZ §1).

Communication between pipeline agents uses exclusively ``asyncio.Queue``.
No third-party libraries.

API:
* :meth:`AsyncEventBus.publish` — enqueue a typed :class:`BusEvent`
* :meth:`AsyncEventBus.subscribe` — register for event type(s); returns a subscription queue
* :meth:`AsyncEventBus.unsubscribe` — remove a subscription
* :meth:`AsyncEventBus.broadcast` — fan-out one event to all matching subscribers
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from core.event_types import BusEvent, EventType

logger = logging.getLogger("tubedub.event_bus")

# Downstream agent hint for log formatting (TZ §6).
_EVENT_TARGETS: dict[str, str] = {
    EventType.TRANSLATION_COMPLETED.value: "Cleaner",
    EventType.SEGMENTS_ALIGNED.value: "Timing",
    EventType.TIMING_COMPLETED.value: "Voice",
    EventType.VOICE_COMPLETED.value: "Mix",
    EventType.MIX_COMPLETED.value: "Export",
    EventType.EXPORT_COMPLETED.value: "Pipeline",
}


@dataclass
class Subscription:
    """One subscriber channel backed by asyncio.Queue."""

    subscription_id: str
    event_types: frozenset[str]
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    agent_name: str = ""
    created_at: float = field(default_factory=time.time)


class AsyncEventBus:
    """In-process async event bus — all agents communicate only through this."""

    def __init__(self, *, max_queue_size: int = 0) -> None:
        self._max_qsize = max_queue_size  # 0 = unlimited
        self._subscriptions: dict[str, Subscription] = {}
        self._by_type: dict[str, list[str]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._history: list[dict[str, Any]] = []
        self._max_history = 1000
        self._running = True
        self._counter = 0

    @property
    def running(self) -> bool:
        return self._running

    def subscribe(
        self,
        event_name: str | list[str] | tuple[str, ...],
        *,
        agent_name: str = "",
    ) -> Subscription:
        """Subscribe to one or more event types. Returns a subscription with its own queue."""
        if isinstance(event_name, str):
            types = frozenset({event_name, "*"})
        else:
            types = frozenset(list(event_name) + ["*"])

        self._counter += 1
        sub_id = f"sub-{self._counter}-{agent_name or 'anon'}"
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_qsize)
        sub = Subscription(
            subscription_id=sub_id,
            event_types=types,
            queue=q,
            agent_name=agent_name,
        )
        self._subscriptions[sub_id] = sub
        for et in types:
            if et != "*":
                self._by_type[et].append(sub_id)
        return sub

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription. Returns True if it existed."""
        sub = self._subscriptions.pop(subscription_id, None)
        if not sub:
            return False
        for et in sub.event_types:
            if et == "*":
                continue
            ids = self._by_type.get(et, [])
            self._by_type[et] = [i for i in ids if i != subscription_id]
        return True

    async def publish(self, event: BusEvent) -> int:
        """Publish a typed event to all matching subscribers. Returns subscriber count."""
        if not isinstance(event, BusEvent):
            raise TypeError("publish() requires a BusEvent instance, not raw dict/str")
        if not self._running:
            logger.warning("[BUS] publish ignored — bus stopped type=%s", event.event_type)
            return 0
        return await self.broadcast(event)

    async def broadcast(self, event: BusEvent) -> int:
        """Fan-out event to every subscriber whose filter matches."""
        delivered = await self._deliver(event)
        self._record_history(event, delivered)
        self._log_event(event, delivered)
        return delivered

    async def _deliver(self, event: BusEvent) -> int:
        et = event.event_type
        sub_ids: set[str] = set(self._by_type.get(et, []))
        sub_ids.update(self._by_type.get("*", []))

        delivered = 0
        for sid in list(sub_ids):
            sub = self._subscriptions.get(sid)
            if not sub:
                continue
            if et not in sub.event_types and "*" not in sub.event_types:
                continue
            try:
                sub.queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning(
                    "[BUS] queue full sub=%s type=%s chunk=%s",
                    sid,
                    et,
                    event.chunk_id,
                )
        return delivered

    def _record_history(self, event: BusEvent, delivered: int) -> None:
        entry = event.to_dict()
        entry["subscribers"] = delivered
        entry["ts_ms"] = int(event.timestamp * 1000)
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

    def _log_event(self, event: BusEvent, delivered: int) -> None:
        agent = event.source_agent or "Bus"
        target = _EVENT_TARGETS.get(event.event_type, "")
        chunk = f"Chunk {event.chunk_id}" if event.chunk_id else ""
        if target:
            logger.info(
                "[EVENT] %s %s %s → %s",
                agent,
                event.event_type,
                chunk,
                target,
            )
        else:
            logger.info(
                "[BUS] %s %s %s Subscribers: %d",
                event.event_type,
                chunk,
                f"project={event.project_id[:8]}",
                delivered,
            )

    async def shutdown(self) -> None:
        """Stop the bus and notify all subscribers."""
        self._running = False
        shutdown_evt = BusEvent.create(
            EventType.SHUTDOWN,
            project_id="system",
            chunk_id=-1,
            source_agent="bus",
        )
        await self.broadcast(shutdown_evt)

    def history(self, *, event_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        items = self._history
        if event_type:
            items = [e for e in items if e.get("event_type") == event_type]
        return list(items[-limit:])


# Module-level singleton (per pipeline run, use EventPipelineRunner to create isolated bus).
_global_bus: AsyncEventBus | None = None


def get_event_bus() -> AsyncEventBus:
    global _global_bus
    if _global_bus is None:
        _global_bus = AsyncEventBus()
    return _global_bus


def reset_event_bus() -> AsyncEventBus:
    """Create a fresh bus (for tests / new pipeline run)."""
    global _global_bus
    _global_bus = AsyncEventBus()
    return _global_bus
