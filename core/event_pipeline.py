"""Event-driven pipeline coordinator (TZ §5–8).

Starts all agents as concurrent ``asyncio`` tasks, kicks off processing via
``publish()``, and waits for completion — no direct inter-agent calls.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from core.event_agents import (
    cleaner_handler,
    export_handler,
    mix_handler,
    run_agent_loop,
    start_all_agents,
    timing_handler,
    translator_handler,
    voice_handler,
)
from core.event_bus import AsyncEventBus, reset_event_bus
from core.event_types import BusEvent, EventType

logger = logging.getLogger("tubedub.event_pipeline")


def event_bus_enabled() -> bool:
    return str(os.getenv("VM_EVENT_BUS", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def orchestrator_enabled() -> bool:
    """AI Orchestrator supervises agents (TZ #2). Default on."""
    return str(os.getenv("VM_ORCHESTRATOR", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def pipeline_engine_enabled() -> bool:
    """Adaptive chunk conveyor (TZ #4). Default on."""
    return str(os.getenv("VM_PIPELINE_ENGINE", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


@dataclass
class PipelineRunConfig:
    """Inputs for an event-bus pipeline run."""

    project_id: str
    source_segments: list[str] = field(default_factory=list)
    timing_map: list[Any] = field(default_factory=list)
    source_lang: str = "en"
    target_lang: str = "ru"
    app_dir: Any = None
    translate_meta: list[Any] = field(default_factory=list)
    # Stage control
    agents: tuple[str, ...] = (
        "translator",
        "cleaner",
        "timing",
        "voice",
        "mix",
        "export",
    )
    skip_timing_adapt: bool = False
    skip_mix: bool = True
    subtitles_only: bool = False
    tts_engine: str = "edge-offline"
    voice: str = ""
    rate: str = "-5%"
    pitch: str | None = None
    export_path: str = ""
    timed_audio_path: str = ""
    timed_audio_obj: Any = None
    timeout_sec: float = 3600.0
    progress_cb: Callable[[int, int], None] | None = None


@dataclass
class PipelineRunResult:
    ok: bool
    segments: list[str] = field(default_factory=list)
    translate_meta: list[Any] = field(default_factory=list)
    translation_audits: list[Any] = field(default_factory=list)
    translation_meta: dict[str, Any] = field(default_factory=dict)
    tts_files: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bus_history: list[dict[str, Any]] = field(default_factory=list)


_AGENT_HANDLERS = {
    "translator": translator_handler,
    "cleaner": cleaner_handler,
    "timing": timing_handler,
    "voice": voice_handler,
    "mix": mix_handler,
    "export": export_handler,
}


def _start_agents(
    bus: AsyncEventBus,
    ctx: dict[str, Any],
    agent_names: tuple[str, ...],
) -> list[asyncio.Task]:
    tasks: list[asyncio.Task] = []

    async def _wrap(name: str, handler_fn: Any) -> None:
        async def _h(event: BusEvent) -> None:
            await handler_fn(event, bus, ctx)

        await run_agent_loop(bus, name, _h)

    for name in agent_names:
        handler = _AGENT_HANDLERS.get(name)
        if not handler:
            continue
        tasks.append(asyncio.create_task(_wrap(name, handler), name=f"agent-{name}"))
    return tasks


async def _wait_for_event(
    bus: AsyncEventBus,
    event_types: tuple[str, ...],
    *,
    timeout: float,
) -> BusEvent | None:
    sub = bus.subscribe(list(event_types), agent_name="coordinator")
    try:
        return await asyncio.wait_for(sub.queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        bus.unsubscribe(sub.subscription_id)


async def run_pipeline_async(config: PipelineRunConfig) -> PipelineRunResult:
    """Run the configured agent chain entirely via Event Bus."""
    bus = reset_event_bus()
    ctx: dict[str, Any] = {
        "source_segments": list(config.source_segments),
        "timing_map": list(config.timing_map),
        "source_lang": config.source_lang,
        "target_lang": config.target_lang,
        "app_dir": config.app_dir,
        "translate_meta": list(config.translate_meta),
        "skip_timing_adapt": config.skip_timing_adapt,
        "skip_mix": config.skip_mix,
        "subtitles_only": config.subtitles_only,
        "tts_engine": config.tts_engine,
        "voice": config.voice,
        "rate": config.rate,
        "pitch": config.pitch,
        "export_path": config.export_path,
        "timed_audio_path": config.timed_audio_path,
        "timed_audio_obj": config.timed_audio_obj,
        "progress_cb": config.progress_cb,
    }

    errors: list[str] = []
    orch = None
    agent_tasks: list[asyncio.Task] = []
    if orchestrator_enabled():
        # AI Orchestrator supervises agents (lifecycle, restart, resources).
        from core.orchestrator import build_default_orchestrator, set_orchestrator

        orch = build_default_orchestrator(bus, ctx, agents=config.agents)
        set_orchestrator(orch)
        await orch.start(project_id=config.project_id)
    else:
        agent_tasks = _start_agents(bus, ctx, config.agents)

    # Error monitor — agent failures do not stop the bus (TZ §7)
    async def _error_monitor() -> None:
        sub = bus.subscribe([EventType.AGENT_ERROR.value], agent_name="error_monitor")
        try:
            while bus.running:
                try:
                    ev: BusEvent = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                err = str(ev.payload.get("error") or "unknown")
                errors.append(f"{ev.payload.get('agent')}: {err}")
                logger.warning(
                    "[BUS] agent_error %s chunk=%s: %s",
                    ev.payload.get("agent"),
                    ev.chunk_id,
                    err,
                )
        finally:
            bus.unsubscribe(sub.subscription_id)

    error_task = asyncio.create_task(_error_monitor(), name="error-monitor")

    # Determine completion event and subscribe BEFORE publishing start (avoid race).
    if "export" in config.agents:
        done_type = EventType.PIPELINE_COMPLETED.value
    elif "voice" in config.agents:
        done_type = EventType.VOICE_COMPLETED.value
    elif "timing" in config.agents:
        done_type = EventType.TIMING_COMPLETED.value
    elif "cleaner" in config.agents:
        done_type = EventType.SEGMENTS_ALIGNED.value
    else:
        done_type = EventType.TRANSLATION_COMPLETED.value

    done_sub = bus.subscribe(
        [done_type, EventType.PIPELINE_FAILED.value], agent_name="coordinator"
    )

    # Allow agents to subscribe before the first event (TZ §5).
    await asyncio.sleep(0.05)

    await bus.publish(
        BusEvent.create(
            EventType.PIPELINE_STARTED,
            project_id=config.project_id,
            chunk_id=0,
            payload={
                "source_segments": config.source_segments,
                "timing_map": config.timing_map,
                "source_lang": config.source_lang,
                "target_lang": config.target_lang,
            },
            source_agent="coordinator",
        )
    )

    try:
        done_event = await asyncio.wait_for(done_sub.queue.get(), timeout=config.timeout_sec)
    except asyncio.TimeoutError:
        done_event = None
    finally:
        bus.unsubscribe(done_sub.subscription_id)

    if orch is not None:
        orch_status = orch.get_status()
        ctx["orchestrator_status"] = orch_status
        await orch.shutdown()
        from core.orchestrator import set_orchestrator

        set_orchestrator(None)

    await bus.shutdown()
    error_task.cancel()
    for t in agent_tasks:
        t.cancel()
    await asyncio.gather(*agent_tasks, error_task, return_exceptions=True)

    segments = list(ctx.get("segments") or ctx.get("translated_segments") or [])
    ok = done_event is not None and done_event.event_type != EventType.PIPELINE_FAILED.value

    if not ok and not errors:
        errors.append(f"timeout waiting for {done_type}")

    return PipelineRunResult(
        ok=ok,
        segments=segments,
        translate_meta=list(ctx.get("translate_meta") or config.translate_meta),
        translation_audits=list(ctx.get("translation_audits") or []),
        translation_meta=dict(ctx.get("translation_meta") or {}),
        tts_files=list(ctx.get("tts_files") or []),
        errors=errors,
        bus_history=bus.history(limit=200),
    )


def run_pipeline_sync(config: PipelineRunConfig) -> PipelineRunResult:
    """Sync entry point for the threaded dub worker."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(run_pipeline_async(config))
    finally:
        loop.close()


def run_translation_chain_sync(
    *,
    task_id: str,
    source_segments: list[str],
    timing_map: list[Any],
    source_lang: str,
    target_lang: str,
    app_dir: Any,
    translate_meta: list[Any] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    timeout_sec: float = 3600.0,
) -> PipelineRunResult:
    """Translator → Cleaner chain (replaces direct calls in ``_prepare_translated_segments``)."""
    if translate_meta is None:
        translate_meta = []
    total = len(source_segments)
    if progress_cb and total:
        progress_cb(0, total)

    result = run_pipeline_sync(
        PipelineRunConfig(
            project_id=task_id,
            source_segments=source_segments,
            timing_map=timing_map,
            source_lang=source_lang,
            target_lang=target_lang,
            app_dir=app_dir,
            translate_meta=translate_meta,
            agents=("translator", "cleaner"),
            timeout_sec=timeout_sec,
            progress_cb=progress_cb,
        )
    )

    if progress_cb and total:
        progress_cb(total, total)
    return result


def run_chunk_pipeline_sync(
    *,
    task_id: str,
    source_segments: list[str],
    timing_map: list[Any],
    source_lang: str = "en",
    target_lang: str = "ru",
    app_dir: Any = None,
    checkpoint_path: str = "",
    agents: tuple[str, ...] = (
        "cleaner",
        "translator",
        "review",
        "timing",
        "voice",
        "mix",
        "export",
    ),
    skip_mix: bool = True,
    progress_cb: Callable[[int, int], None] | None = None,
    resume: bool = False,
) -> PipelineRunResult:
    """Run the adaptive chunk conveyor (TZ #4).

    When ``VM_PIPELINE_ENGINE=1`` (default), segments are split into adaptive
    chunks and processed through all stages in parallel. Falls back to the
    event-bus pipeline when disabled.
    """
    if not pipeline_engine_enabled():
        return run_pipeline_sync(
            PipelineRunConfig(
                project_id=task_id,
                source_segments=source_segments,
                timing_map=timing_map,
                source_lang=source_lang,
                target_lang=target_lang,
                app_dir=app_dir,
                agents=agents,
                skip_mix=skip_mix,
                progress_cb=progress_cb,
            )
        )

    from core.pipeline_engine import PipelineEngineConfig, run_pipeline_engine

    ctx: dict[str, Any] = {
        "source_lang": source_lang,
        "target_lang": target_lang,
        "skip_mix": skip_mix,
        "tts_files": [],
    }
    config = PipelineEngineConfig(
        project_id=task_id,
        source_segments=source_segments,
        timing_map=timing_map,
        source_lang=source_lang,
        target_lang=target_lang,
        app_dir=app_dir,
        stages=agents,
        checkpoint_path=checkpoint_path,
        ctx=ctx,
    )
    result = run_pipeline_engine(config, resume=resume)

    # Performance Optimizer self-learning (TZ #7 §8) — wrapper only, no engine change.
    try:
        from core.performance_optimizer import optimizer_enabled, get_performance_optimizer
        from core.performance_monitor import get_performance_monitor

        if optimizer_enabled():
            mon = get_performance_monitor()
            metrics = mon.averages()
            metrics["chunks_processed"] = result.chunks_processed
            metrics["segments"] = len(result.segments)
            get_performance_optimizer(app_dir=app_dir).record_film(task_id, metrics)
    except Exception:
        pass

    # Monitoring Center self-diagnosis (TZ #8 §17) — wrapper only.
    try:
        from core.monitoring_center import get_monitor, monitoring_enabled

        if monitoring_enabled():
            mon = get_monitor(app_dir=app_dir)
            mon.finalize_project(
                task_id,
                errors=result.errors,
                duration_s=0.0,
                speed=len(result.segments) / max(1.0, 1.0),
                status="completed" if result.ok else "failed",
            )
    except Exception:
        pass

    # Dev Assistant continuous self-diagnostics (TZ #10 §9) — wrapper only.
    try:
        from core.dev_assistant import assistant_enabled, get_dev_assistant

        if assistant_enabled():
            get_dev_assistant(app_dir=app_dir).self_diagnose(
                task_id,
                metrics={"segments": len(result.segments), "ok": result.ok},
            )
    except Exception:
        pass

    if progress_cb:
        total = len(source_segments)
        done = result.chunks_processed
        progress_cb(done, total)

    return PipelineRunResult(
        ok=result.ok,
        segments=result.segments,
        tts_files=result.tts_files,
        errors=result.errors,
    )
