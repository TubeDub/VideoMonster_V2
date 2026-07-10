"""AI Orchestrator — the conductor of TubeDub (TZ #2).

The orchestrator does NOT process video, translation or audio. It only manages
agents: lifecycle, queues, priorities, resources, memory, stalls, statistics and
fault tolerance. All coordination flows through the Event Bus (Stage 1).

Responsibilities (TZ #2):
1. Agent lifecycle — start / track / auto-restart / shutdown; states Idle,
   Waiting, Working, Paused, Restarting, Failed.
2. Dynamic queue dispatch — no fixed execution order.
3. Stall control — detect idle agents, walk the chain to find the cause.
4. Resource monitoring — CPU / GPU / RAM / VRAM / queue depths / active agents.
5. Priority system — Critical / High / Normal / Low / Background.
6. Memory management — throttle when RAM/VRAM > 90%.
7. Concurrency limits — computed automatically from the host.
8. Global statistics per project.
9. Public API — request_chunk / finish_chunk / pause / resume / get_status.
10. Fault tolerance — register error, restart agent, requeue chunk, keep going.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Awaitable, Callable

from core.event_bus import AsyncEventBus, Subscription
from core.event_types import BusEvent, EventType
from core.resource_monitor import ResourceMonitor
from engines.pipeline_orchestrator.resource_planner import get_planner

logger = logging.getLogger("tubedub.orchestrator")


class AgentState(str, Enum):
    IDLE = "idle"
    WAITING = "waiting"
    WORKING = "working"
    PAUSED = "paused"
    RESTARTING = "restarting"
    FAILED = "failed"


class TaskPriority(IntEnum):
    """Lower value = scheduled first."""

    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


# Stage → priority (TZ #2 §5).
STAGE_PRIORITY: dict[str, TaskPriority] = {
    "stt": TaskPriority.CRITICAL,
    "whisper": TaskPriority.CRITICAL,
    "translation": TaskPriority.CRITICAL,
    "translator": TaskPriority.CRITICAL,
    "cleaner": TaskPriority.HIGH,
    "timing": TaskPriority.HIGH,
    "tts": TaskPriority.NORMAL,
    "voice": TaskPriority.NORMAL,
    "mix": TaskPriority.NORMAL,
    "export": TaskPriority.NORMAL,
    "review": TaskPriority.LOW,
    "reports": TaskPriority.BACKGROUND,
    "indexing": TaskPriority.BACKGROUND,
    "logs": TaskPriority.BACKGROUND,
    "analytics": TaskPriority.BACKGROUND,
}

# Linear dependency for stall cause analysis (upstream lookup).
AGENT_UPSTREAM: dict[str, str] = {
    "cleaner": "translator",
    "timing": "cleaner",
    "voice": "timing",
    "mix": "voice",
    "export": "mix",
}


@dataclass
class AgentSupervisor:
    """Tracks one managed agent."""

    name: str
    priority: TaskPriority = TaskPriority.NORMAL
    state: AgentState = AgentState.IDLE
    task: asyncio.Task | None = None
    subscription: Subscription | None = None
    restart_count: int = 0
    error_count: int = 0
    processed: int = 0
    last_activity: float = field(default_factory=time.monotonic)
    last_busy_ms: float = 0.0
    total_busy_ms: float = 0.0
    total_wait_ms: float = 0.0
    paused: bool = False
    max_restarts: int = 5

    def queue_depth(self) -> int:
        if self.subscription is None:
            return 0
        try:
            return self.subscription.queue.qsize()
        except Exception:
            return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority.name,
            "state": self.state.value,
            "restart_count": self.restart_count,
            "error_count": self.error_count,
            "processed": self.processed,
            "queue_depth": self.queue_depth(),
            "idle_seconds": round(time.monotonic() - self.last_activity, 1),
            "avg_busy_ms": round(self.total_busy_ms / self.processed, 1) if self.processed else 0.0,
            "total_wait_ms": round(self.total_wait_ms, 1),
            "paused": self.paused,
        }


@dataclass
class OrchestratorStats:
    """Global per-project statistics (TZ #2 §8)."""

    project_id: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    total_errors: int = 0
    total_restarts: int = 0
    chunks_requested: int = 0
    chunks_finished: int = 0
    chunks_requeued: int = 0
    stall_recoveries: int = 0
    cpu_samples: deque = field(default_factory=lambda: deque(maxlen=256))
    gpu_samples: deque = field(default_factory=lambda: deque(maxlen=256))
    ram_samples: deque = field(default_factory=lambda: deque(maxlen=256))
    vram_samples: deque = field(default_factory=lambda: deque(maxlen=256))

    @staticmethod
    def _avg(d: deque) -> float:
        return round(sum(d) / len(d), 1) if d else 0.0

    def to_dict(self, agents: dict[str, AgentSupervisor]) -> dict[str, Any]:
        elapsed = (self.finished_at or time.time()) - self.started_at if self.started_at else 0.0
        agent_times = {
            n: (round(a.total_busy_ms / a.processed, 1) if a.processed else 0.0)
            for n, a in agents.items()
        }
        return {
            "project_id": self.project_id,
            "elapsed_sec": round(elapsed, 1),
            "total_errors": self.total_errors,
            "total_restarts": self.total_restarts,
            "chunks_requested": self.chunks_requested,
            "chunks_finished": self.chunks_finished,
            "chunks_requeued": self.chunks_requeued,
            "stall_recoveries": self.stall_recoveries,
            "avg_cpu_percent": self._avg(self.cpu_samples),
            "avg_gpu_percent": self._avg(self.gpu_samples),
            "avg_ram_percent": self._avg(self.ram_samples),
            "avg_vram_percent": self._avg(self.vram_samples),
            "avg_agent_busy_ms": agent_times,
        }


@dataclass
class _Chunk:
    chunk_id: int
    stage: str
    priority: TaskPriority
    payload: dict[str, Any]
    attempts: int = 0
    enqueued_at: float = field(default_factory=time.monotonic)


# Handler signature: (event, bus, ctx) -> awaitable
AgentHandler = Callable[[BusEvent, AsyncEventBus, dict[str, Any]], Awaitable[None]]


class AIOrchestrator:
    """Central conductor — manages agents; never processes media itself."""

    def __init__(
        self,
        bus: AsyncEventBus,
        ctx: dict[str, Any] | None = None,
        *,
        resource_monitor: ResourceMonitor | None = None,
        stall_threshold_s: float = 15.0,
        resource_interval_s: float = 3.0,
        ram_limit: float = 90.0,
        vram_limit: float = 90.0,
    ) -> None:
        self.bus = bus
        self.ctx = ctx if ctx is not None else {}
        self.monitor = resource_monitor or ResourceMonitor()
        self.stall_threshold_s = stall_threshold_s
        self.resource_interval_s = resource_interval_s
        self.ram_limit = ram_limit
        self.vram_limit = vram_limit

        self._agents: dict[str, AgentSupervisor] = {}
        self._handlers: dict[str, AgentHandler] = {}
        self._subscriptions: dict[str, tuple[str, ...]] = {}
        self._chunk_queues: dict[TaskPriority, deque[_Chunk]] = defaultdict(deque)
        self._inflight: dict[int, _Chunk] = {}
        self._lock = asyncio.Lock()
        self.stats = OrchestratorStats()
        self._running = False
        self._bg_tasks: list[asyncio.Task] = []
        self._max_concurrent = self._compute_concurrency_limit()
        self._active_working = 0

    # ── Concurrency / limits (TZ #2 §7) ──────────────────────────────

    def _compute_concurrency_limit(self) -> int:
        snap = get_planner().snapshot()
        if snap.gpu_available:
            return max(2, snap.cpu_cores)
        # CPU-only: cap heavy concurrent processes (≈ cores - 1, min 1)
        return max(1, snap.cpu_cores - 1)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    # ── Agent registration & lifecycle (TZ #2 §1) ────────────────────

    def register_agent(
        self,
        name: str,
        handler: AgentHandler,
        *,
        subscriptions: tuple[str, ...] = (),
        priority: TaskPriority | None = None,
    ) -> None:
        prio = priority or STAGE_PRIORITY.get(name, TaskPriority.NORMAL)
        self._agents[name] = AgentSupervisor(name=name, priority=prio)
        self._handlers[name] = handler
        self._subscriptions[name] = subscriptions

    def _set_state(self, name: str, state: AgentState) -> None:
        sup = self._agents.get(name)
        if sup and sup.state != state:
            sup.state = state

    async def _run_supervised(self, name: str) -> None:
        """Run one agent with lifecycle tracking + auto-restart (TZ #2 §1/§10)."""
        sup = self._agents[name]
        handler = self._handlers[name]
        sub_types = self._subscriptions.get(name, ())

        while self._running and self.bus.running:
            sub = self.bus.subscribe(list(sub_types), agent_name=name)
            sup.subscription = sub
            sup.state = AgentState.WAITING
            try:
                await self._agent_loop(name, sub, handler)
                break  # normal exit (bus stopped)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                sup.error_count += 1
                self.stats.total_errors += 1
                sup.state = AgentState.RESTARTING
                if sup.restart_count >= sup.max_restarts:
                    sup.state = AgentState.FAILED
                    logger.error(
                        "[ORCH] agent %s exceeded max restarts (%d) — FAILED: %s",
                        name,
                        sup.max_restarts,
                        exc,
                    )
                    break
                sup.restart_count += 1
                self.stats.total_restarts += 1
                backoff = min(2.0 * sup.restart_count, 10.0)
                logger.warning(
                    "[ORCH] restarting agent %s (attempt %d) after %.1fs: %s",
                    name,
                    sup.restart_count,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)
            finally:
                if sup.subscription:
                    self.bus.unsubscribe(sup.subscription.subscription_id)
                    sup.subscription = None

        if sup.state not in (AgentState.FAILED,):
            sup.state = AgentState.IDLE

    async def _agent_loop(self, name: str, sub: Subscription, handler: AgentHandler) -> None:
        sup = self._agents[name]
        while self._running and self.bus.running:
            wait_start = time.monotonic()
            try:
                event: BusEvent = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if sup.state == AgentState.WORKING:
                    sup.state = AgentState.WAITING
                continue
            sup.total_wait_ms += (time.monotonic() - wait_start) * 1000.0

            if event.event_type == EventType.SHUTDOWN.value:
                break
            if sup.paused:
                # Put back and wait until resumed (TZ #2 §9 pause)
                await self._requeue_event(sub, event)
                await asyncio.sleep(0.2)
                continue
            if not self._should_handle(name, event):
                continue

            # Concurrency gate for heavy agents (TZ #2 §7)
            await self._acquire_slot(name)
            sup.state = AgentState.WORKING
            busy_start = time.monotonic()
            try:
                await handler(event, self.bus, self.ctx)
                sup.processed += 1
            except Exception as exc:
                sup.error_count += 1
                self.stats.total_errors += 1
                logger.exception("[ORCH] %s handler error chunk=%s: %s", name, event.chunk_id, exc)
                await self._handle_agent_error(name, event, str(exc))
            finally:
                busy_ms = (time.monotonic() - busy_start) * 1000.0
                sup.last_busy_ms = busy_ms
                sup.total_busy_ms += busy_ms
                sup.last_activity = time.monotonic()
                self._release_slot(name)
                sup.state = AgentState.WAITING

    async def _requeue_event(self, sub: Subscription, event: BusEvent) -> None:
        try:
            sub.queue.put_nowait(event)
        except Exception:
            pass

    def _should_handle(self, name: str, event: BusEvent) -> bool:
        subs = self._subscriptions.get(name, ())
        if event.event_type == EventType.PIPELINE_STARTED.value:
            return name in ("translator", "stt", "whisper")
        return event.event_type in subs

    # ── Concurrency slots ────────────────────────────────────────────

    async def _acquire_slot(self, name: str) -> None:
        # Background/low priority yields under memory pressure or saturation.
        sup = self._agents[name]
        while self._running:
            async with self._lock:
                if self._active_working < self._max_concurrent:
                    self._active_working += 1
                    return
            await asyncio.sleep(0.05)

    def _release_slot(self, name: str) -> None:
        if self._active_working > 0:
            self._active_working -= 1

    async def _handle_agent_error(self, name: str, event: BusEvent, error: str) -> None:
        """Fault tolerance: register + requeue the chunk + keep going (TZ #2 §10)."""
        await self.bus.publish(
            BusEvent.create(
                EventType.AGENT_ERROR,
                project_id=event.project_id,
                chunk_id=event.chunk_id,
                payload={"agent": name, "error": error, "recoverable": True},
                source_agent=name,
            )
        )
        chunk = self._inflight.pop(event.chunk_id, None)
        if chunk is not None:
            chunk.attempts += 1
            self.stats.chunks_requeued += 1
            self._chunk_queues[chunk.priority].append(chunk)

    # ── Background loops (TZ #2 §3/§4/§6) ─────────────────────────────

    async def _resource_loop(self) -> None:
        while self._running:
            s = await asyncio.to_thread(self.monitor.sample)
            self.stats.cpu_samples.append(s.cpu_percent)
            self.stats.ram_samples.append(s.ram_percent)
            if s.gpu_available:
                self.stats.gpu_samples.append(s.gpu_percent)
                self.stats.vram_samples.append(s.vram_percent)
            self._apply_memory_pressure(s)
            await asyncio.sleep(self.resource_interval_s)

    def _apply_memory_pressure(self, sample) -> None:
        pressure = (
            sample.ram_percent >= self.ram_limit
            or (sample.gpu_available and sample.vram_percent >= self.vram_limit)
        )
        if pressure:
            new_limit = max(1, self._max_concurrent - 1)
            if new_limit != self._max_concurrent:
                logger.warning(
                    "[ORCH] memory pressure (RAM %.0f%% / VRAM %.0f%%) — "
                    "reducing concurrency %d -> %d, pausing background agents",
                    sample.ram_percent,
                    sample.vram_percent,
                    self._max_concurrent,
                    new_limit,
                )
                self._max_concurrent = new_limit
            for sup in self._agents.values():
                if sup.priority >= TaskPriority.LOW:
                    sup.paused = True
        else:
            base = self._compute_concurrency_limit()
            if self._max_concurrent < base:
                self._max_concurrent = base
            for sup in self._agents.values():
                if sup.priority >= TaskPriority.LOW and not self.ctx.get("keep_paused"):
                    sup.paused = False

    async def _stall_loop(self) -> None:
        while self._running:
            await asyncio.sleep(max(1.0, self.stall_threshold_s / 3.0))
            now = time.monotonic()
            for name, sup in self._agents.items():
                if sup.state != AgentState.WAITING or sup.paused:
                    continue
                idle = now - sup.last_activity
                if idle < self.stall_threshold_s:
                    continue
                cause = self._diagnose_stall(name)
                logger.info(
                    "[ORCH] stall check %s idle=%.0fs → %s", name, idle, cause
                )
                if cause.get("recovered"):
                    self.stats.stall_recoveries += 1

    def _diagnose_stall(self, name: str) -> dict[str, Any]:
        """Walk upstream to find why an agent is idle (TZ #2 §3)."""
        chain: list[dict[str, Any]] = []
        cur = name
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            sup = self._agents.get(cur)
            if not sup:
                break
            depth = sup.queue_depth()
            chain.append({"agent": cur, "state": sup.state.value, "queue": depth})
            if depth > 0:
                return {"cause": f"{cur}_has_pending_work", "chain": chain, "recovered": False}
            upstream = AGENT_UPSTREAM.get(cur)
            if not upstream:
                return {"cause": "upstream_complete_or_source", "chain": chain, "recovered": False}
            up = self._agents.get(upstream)
            if up and up.state in (AgentState.WORKING, AgentState.WAITING):
                return {
                    "cause": f"waiting_on_{upstream}",
                    "chain": chain,
                    "recovered": False,
                }
            cur = upstream
        return {"cause": "unknown", "chain": chain, "recovered": False}

    # ── Public API (TZ #2 §9) ────────────────────────────────────────

    def request_chunk(self, agent: str | None = None) -> dict[str, Any] | None:
        """Return the next highest-priority chunk (or None). Pull-based API."""
        self.stats.chunks_requested += 1
        for prio in sorted(self._chunk_queues.keys()):
            q = self._chunk_queues[prio]
            if q:
                chunk = q.popleft()
                self._inflight[chunk.chunk_id] = chunk
                return {
                    "chunk_id": chunk.chunk_id,
                    "stage": chunk.stage,
                    "priority": chunk.priority.name,
                    "payload": chunk.payload,
                    "attempts": chunk.attempts,
                }
        return None

    def finish_chunk(self, chunk_id: int, *, ok: bool = True) -> None:
        chunk = self._inflight.pop(chunk_id, None)
        if ok:
            self.stats.chunks_finished += 1
        elif chunk is not None:
            chunk.attempts += 1
            self.stats.chunks_requeued += 1
            self._chunk_queues[chunk.priority].append(chunk)

    def submit_chunk(
        self,
        chunk_id: int,
        stage: str,
        payload: dict[str, Any],
        *,
        priority: TaskPriority | None = None,
    ) -> None:
        prio = priority or STAGE_PRIORITY.get(stage, TaskPriority.NORMAL)
        self._chunk_queues[prio].append(
            _Chunk(chunk_id=chunk_id, stage=stage, priority=prio, payload=payload)
        )

    def pause(self, agent: str | None = None) -> None:
        if agent:
            sup = self._agents.get(agent)
            if sup:
                sup.paused = True
                sup.state = AgentState.PAUSED
        else:
            for sup in self._agents.values():
                sup.paused = True
                sup.state = AgentState.PAUSED

    def resume(self, agent: str | None = None) -> None:
        if agent:
            sup = self._agents.get(agent)
            if sup:
                sup.paused = False
                sup.state = AgentState.WAITING
        else:
            for sup in self._agents.values():
                sup.paused = False
                sup.state = AgentState.WAITING

    def get_status(self) -> dict[str, Any]:
        sample = self.monitor.last()
        return {
            "running": self._running,
            "max_concurrent": self._max_concurrent,
            "active_working": self._active_working,
            "agents": {n: a.to_dict() for n, a in self._agents.items()},
            "resources": sample.to_dict(),
            "queues": {
                prio.name: len(self._chunk_queues[prio])
                for prio in self._chunk_queues
            },
            "stats": self.stats.to_dict(self._agents),
        }

    # ── Start / stop ─────────────────────────────────────────────────

    async def start(self, project_id: str = "") -> None:
        if self._running:
            return
        self._running = True
        self.stats = OrchestratorStats(project_id=project_id, started_at=time.time())
        for name in self._agents:
            sup = self._agents[name]
            sup.task = asyncio.create_task(
                self._run_supervised(name), name=f"orch-agent-{name}"
            )
        self._bg_tasks = [
            asyncio.create_task(self._resource_loop(), name="orch-resources"),
            asyncio.create_task(self._stall_loop(), name="orch-stall"),
        ]
        logger.info(
            "[ORCH] started project=%s agents=%d max_concurrent=%d",
            project_id,
            len(self._agents),
            self._max_concurrent,
        )

    async def shutdown(self) -> None:
        self._running = False
        self.stats.finished_at = time.time()
        for sup in self._agents.values():
            if sup.task:
                sup.task.cancel()
        for t in self._bg_tasks:
            t.cancel()
        tasks = [s.task for s in self._agents.values() if s.task] + self._bg_tasks
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[ORCH] shutdown project=%s", self.stats.project_id)


def build_default_orchestrator(
    bus: AsyncEventBus,
    ctx: dict[str, Any],
    *,
    agents: tuple[str, ...] = (
        "translator",
        "cleaner",
        "timing",
        "voice",
        "mix",
        "export",
    ),
) -> AIOrchestrator:
    """Wire the standard dubbing agents into a new orchestrator."""
    from core.event_pipeline import _AGENT_HANDLERS
    from core.event_types import AGENT_SUBSCRIPTIONS

    orch = AIOrchestrator(bus, ctx)
    for name in agents:
        handler = _AGENT_HANDLERS.get(name)
        if not handler:
            continue
        orch.register_agent(
            name,
            handler,
            subscriptions=AGENT_SUBSCRIPTIONS.get(name, ()),
        )
    return orch


_singleton: AIOrchestrator | None = None


def get_orchestrator() -> AIOrchestrator | None:
    return _singleton


def set_orchestrator(orch: AIOrchestrator | None) -> None:
    global _singleton
    _singleton = orch
