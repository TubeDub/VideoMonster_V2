"""Tests for the AI Orchestrator (TZ #2)."""

from __future__ import annotations

import asyncio

from core.event_bus import AsyncEventBus, reset_event_bus
from core.event_types import BusEvent, EventType
from core.orchestrator import (
    AgentState,
    AIOrchestrator,
    TaskPriority,
    STAGE_PRIORITY,
)
from core.resource_monitor import ResourceMonitor, ResourceSample


def test_priority_ordering():
    assert TaskPriority.CRITICAL < TaskPriority.HIGH < TaskPriority.NORMAL
    assert TaskPriority.NORMAL < TaskPriority.LOW < TaskPriority.BACKGROUND
    assert STAGE_PRIORITY["translation"] == TaskPriority.CRITICAL
    assert STAGE_PRIORITY["timing"] == TaskPriority.HIGH
    assert STAGE_PRIORITY["tts"] == TaskPriority.NORMAL
    assert STAGE_PRIORITY["review"] == TaskPriority.LOW
    assert STAGE_PRIORITY["analytics"] == TaskPriority.BACKGROUND


def test_chunk_queue_priority_dispatch():
    orch = AIOrchestrator(AsyncEventBus())
    orch.submit_chunk(1, "tts", {"i": 1})           # NORMAL
    orch.submit_chunk(2, "translation", {"i": 2})   # CRITICAL
    orch.submit_chunk(3, "review", {"i": 3})        # LOW
    first = orch.request_chunk()
    assert first["chunk_id"] == 2  # critical served first
    second = orch.request_chunk()
    assert second["chunk_id"] == 1  # normal before low
    third = orch.request_chunk()
    assert third["chunk_id"] == 3
    assert orch.request_chunk() is None
    orch.finish_chunk(2)
    assert orch.stats.chunks_finished == 1


def test_finish_chunk_failure_requeues():
    orch = AIOrchestrator(AsyncEventBus())
    orch.submit_chunk(5, "tts", {"x": 1})
    c = orch.request_chunk()
    assert c["chunk_id"] == 5
    orch.finish_chunk(5, ok=False)
    assert orch.stats.chunks_requeued == 1
    again = orch.request_chunk()
    assert again["chunk_id"] == 5
    assert again["attempts"] == 1


def test_memory_pressure_reduces_concurrency():
    orch = AIOrchestrator(AsyncEventBus(), ram_limit=90.0)
    orch.register_agent("review", _noop_handler, priority=TaskPriority.LOW)
    base = orch.max_concurrent
    high = ResourceSample(ram_percent=95.0)
    orch._apply_memory_pressure(high)
    assert orch.max_concurrent <= base
    assert orch._agents["review"].paused is True
    calm = ResourceSample(ram_percent=10.0)
    orch._apply_memory_pressure(calm)
    assert orch._agents["review"].paused is False


def test_resource_monitor_fallback():
    mon = ResourceMonitor()
    s = mon.sample()
    assert isinstance(s.cpu_percent, float)
    assert 0.0 <= s.ram_percent <= 100.0
    assert isinstance(s.to_dict(), dict)


def test_stall_diagnosis_walks_upstream():
    orch = AIOrchestrator(AsyncEventBus())
    for name in ("translator", "cleaner", "timing", "voice"):
        orch.register_agent(name, _noop_handler)
    # voice waiting; upstream timing/cleaner/translator all idle+empty
    orch._agents["voice"].state = AgentState.WAITING
    diag = orch._diagnose_stall("voice")
    assert "chain" in diag
    assert diag["chain"][0]["agent"] == "voice"


def test_pause_resume_and_status():
    orch = AIOrchestrator(AsyncEventBus())
    orch.register_agent("voice", _noop_handler)
    orch.pause("voice")
    assert orch._agents["voice"].state == AgentState.PAUSED
    orch.resume("voice")
    assert orch._agents["voice"].state == AgentState.WAITING
    status = orch.get_status()
    assert "agents" in status and "resources" in status
    assert "queues" in status and "stats" in status
    assert "voice" in status["agents"]


async def _noop_handler(event, bus, ctx):  # pragma: no cover - trivial
    return None


def test_lifecycle_start_shutdown_and_autorestart():
    async def _run():
        bus = reset_event_bus()
        orch = AIOrchestrator(bus, stall_threshold_s=100.0, resource_interval_s=100.0)

        calls = {"n": 0}

        async def flaky_handler(event, bus_, ctx):
            calls["n"] += 1

        orch.register_agent(
            "translator",
            flaky_handler,
            subscriptions=(EventType.PIPELINE_STARTED.value,),
        )
        await orch.start(project_id="lc-task")
        await asyncio.sleep(0.1)
        assert orch._agents["translator"].state in (
            AgentState.WAITING,
            AgentState.WORKING,
            AgentState.IDLE,
        )
        await bus.publish(
            BusEvent.create(
                EventType.PIPELINE_STARTED,
                project_id="lc-task",
                payload={"source_segments": ["x"]},
                source_agent="coordinator",
            )
        )
        await asyncio.sleep(0.3)
        assert calls["n"] >= 1
        await orch.shutdown()
        assert orch._running is False

    asyncio.run(_run())


def test_fault_tolerance_agent_error_requeues_chunk():
    async def _run():
        bus = reset_event_bus()
        orch = AIOrchestrator(bus, stall_threshold_s=100.0, resource_interval_s=100.0)

        async def boom(event, bus_, ctx):
            raise RuntimeError("kaboom")

        orch.register_agent(
            "translator",
            boom,
            subscriptions=(EventType.PIPELINE_STARTED.value,),
        )
        # Track an in-flight chunk so error handling requeues it.
        orch.submit_chunk(0, "translation", {"src": "x"})
        orch.request_chunk()  # moves chunk 0 into inflight

        err_sub = bus.subscribe([EventType.AGENT_ERROR.value], agent_name="mon")
        await orch.start(project_id="ft-task")
        await asyncio.sleep(0.05)
        await bus.publish(
            BusEvent.create(
                EventType.PIPELINE_STARTED,
                project_id="ft-task",
                chunk_id=0,
                payload={"source_segments": ["x"]},
                source_agent="coordinator",
            )
        )
        got = await asyncio.wait_for(err_sub.queue.get(), timeout=3.0)
        assert got.payload["agent"] == "translator"
        assert orch.stats.total_errors >= 1
        assert orch.stats.chunks_requeued >= 1
        assert bus.running is True  # bus survives agent crash
        await orch.shutdown()

    asyncio.run(_run())
