"""Event Bus agent adapters (TZ §4).

Each agent:
* runs as an independent ``asyncio`` task
* subscribes only to bus events
* calls existing module logic via ``asyncio.to_thread`` (algorithms unchanged)
* publishes result events — never calls another agent directly
* catches all exceptions — errors do not stop the bus or other agents
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

from core.event_bus import AsyncEventBus, Subscription
from core.event_types import (
    AGENT_OUTPUT_EVENT,
    AGENT_SUBSCRIPTIONS,
    BusEvent,
    EventType,
)

logger = logging.getLogger("tubedub.event_agents")


async def _publish_error(
    bus: AsyncEventBus,
    *,
    project_id: str,
    chunk_id: int,
    agent: str,
    error: str,
    recoverable: bool = True,
) -> None:
    await bus.publish(
        BusEvent.create(
            EventType.AGENT_ERROR,
            project_id=project_id,
            chunk_id=chunk_id,
            payload={
                "agent": agent,
                "chunk_id": chunk_id,
                "error": error,
                "recoverable": recoverable,
            },
            source_agent=agent,
        )
    )


def _should_handle(agent: str, event: BusEvent) -> bool:
    allowed = AGENT_SUBSCRIPTIONS.get(agent, ())
    if event.event_type == EventType.SHUTDOWN.value:
        return True
    if event.event_type == EventType.PIPELINE_STARTED.value:
        return agent == "translator"
    return event.event_type in allowed


async def run_agent_loop(
    bus: AsyncEventBus,
    agent_name: str,
    handler: Callable[[BusEvent], Any],
    *,
    subscription: Subscription | None = None,
) -> None:
    """Generic agent loop — waits on queue, dispatches to handler with error isolation."""
    sub = subscription or bus.subscribe(
        list(AGENT_SUBSCRIPTIONS.get(agent_name, ())),
        agent_name=agent_name,
    )
    try:
        while bus.running:
            try:
                event: BusEvent = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if event.event_type == EventType.SHUTDOWN.value:
                break
            if not _should_handle(agent_name, event):
                continue
            try:
                await handler(event)
            except Exception as exc:
                logger.exception(
                    "[EVENT] %s failed chunk=%s: %s",
                    agent_name,
                    event.chunk_id,
                    exc,
                )
                await _publish_error(
                    bus,
                    project_id=event.project_id,
                    chunk_id=event.chunk_id,
                    agent=agent_name,
                    error=str(exc),
                )
    finally:
        bus.unsubscribe(sub.subscription_id)


# ── Agent handlers (wrap existing logic, no algorithm changes) ─────────────


async def translator_handler(event: BusEvent, bus: AsyncEventBus, ctx: dict[str, Any]) -> None:
    if event.event_type not in (
        EventType.TRANSLATION_REQUESTED.value,
        EventType.PIPELINE_STARTED.value,
    ):
        return

    payload = event.payload
    source_segments = list(
        payload.get("source_segments")
        or ctx.get("source_segments")
        or []
    )
    timing_map = list(payload.get("timing_map") or ctx.get("timing_map") or [])
    source_lang = str(
        payload.get("source_lang") or ctx.get("source_lang") or "en"
    )
    target_lang = str(
        payload.get("target_lang") or ctx.get("target_lang") or "ru"
    )
    app_dir = ctx.get("app_dir")
    task_id = event.project_id
    translate_meta: list = list(payload.get("translate_meta") or ctx.get("translate_meta") or [])

    from engines.translation_pipeline import UniversalTranslationPipeline

    def _translate() -> list[str]:
        pipe = UniversalTranslationPipeline(app_dir=app_dir, task_id=task_id)
        progress_cb = ctx.get("progress_cb")
        result = pipe.translate_segments(
            source_segments,
            timing_map,
            source_lang,
            target_lang,
            translate_meta_out=translate_meta,
            progress_cb=progress_cb,
        )
        ctx["translation_meta"] = result.meta
        ctx["translation_audits"] = pipe.quality_log.records_as_dicts()
        pipe.flush_quality_log(
            src=source_lang,
            tgt=target_lang,
            engines=result.meta.get("engines"),
        )
        return list(result.segments)

    translated = await asyncio.to_thread(_translate)

    ctx["translated_segments"] = translated
    ctx["translate_meta"] = translate_meta
    await bus.publish(
        BusEvent.create(
            EventType.TRANSLATION_COMPLETED,
            project_id=event.project_id,
            chunk_id=event.chunk_id,
            payload={
                "segments": translated,
                "timing_map": timing_map,
                "source_lang": source_lang,
                "target_lang": target_lang,
            },
            source_agent="translator",
        )
    )


async def cleaner_handler(event: BusEvent, bus: AsyncEventBus, ctx: dict[str, Any]) -> None:
    if event.event_type not in (
        EventType.TRANSLATION_COMPLETED.value,
        EventType.CLEANER_REQUESTED.value,
    ):
        return
    from engines.cleaner import align_segments_to_timing_map, split_by_timing_map

    payload = event.payload
    segments = list(payload.get("segments") or ctx.get("translated_segments") or [])
    timing_map = list(payload.get("timing_map") or ctx.get("timing_map") or [])

    aligned = await asyncio.to_thread(
        align_segments_to_timing_map, segments, timing_map
    )
    if timing_map and len(aligned) != len(timing_map):
        block = "\n".join(aligned)
        aligned = await asyncio.to_thread(split_by_timing_map, block, timing_map)

    ctx["segments"] = aligned
    await bus.publish(
        BusEvent.create(
            EventType.SEGMENTS_ALIGNED,
            project_id=event.project_id,
            chunk_id=event.chunk_id,
            payload={"segments": aligned, "timing_map": timing_map},
            source_agent="cleaner",
        )
    )


async def timing_handler(event: BusEvent, bus: AsyncEventBus, ctx: dict[str, Any]) -> None:
    if event.event_type not in (
        EventType.SEGMENTS_ALIGNED.value,
        EventType.TIMING_REQUESTED.value,
    ):
        return

    payload = event.payload
    segments = list(payload.get("segments") or ctx.get("segments") or [])
    timing_map = list(payload.get("timing_map") or ctx.get("timing_map") or [])
    source_segments = list(
        payload.get("source_segments") or ctx.get("source_segments") or []
    )
    target_lang = str(payload.get("target_lang") or ctx.get("target_lang") or "ru")
    skip = bool(ctx.get("skip_timing_adapt"))

    records: list = []
    if not skip and timing_map and segments:
        from engines.timing_aware_translation import adapt_segments_to_timing

        app_dir = ctx.get("app_dir")

        def _adapt() -> tuple[list[str], list]:
            adapted, recs = adapt_segments_to_timing(
                segments,
                timing_map,
                source_segments,
                src_lang=str(ctx.get("source_lang") or "en"),
                tgt_lang=target_lang,
                task_id=event.project_id,
            )
            return adapted, recs

        segments, records = await asyncio.to_thread(_adapt)

    ctx["segments"] = segments
    ctx["timing_records"] = records
    await bus.publish(
        BusEvent.create(
            EventType.TIMING_COMPLETED,
            project_id=event.project_id,
            chunk_id=event.chunk_id,
            payload={
                "segments": segments,
                "timing_map": timing_map,
                "records": records,
            },
            source_agent="timing",
        )
    )


async def voice_handler(event: BusEvent, bus: AsyncEventBus, ctx: dict[str, Any]) -> None:
    if event.event_type not in (
        EventType.TIMING_COMPLETED.value,
        EventType.VOICE_REQUESTED.value,
    ):
        return

    payload = event.payload
    segments = list(payload.get("segments") or ctx.get("segments") or [])
    timing_map = list(payload.get("timing_map") or ctx.get("timing_map") or [])
    target_lang = str(payload.get("target_lang") or ctx.get("target_lang") or "ru")
    tts_engine = str(payload.get("tts_engine") or ctx.get("tts_engine") or "edge-offline")
    voice = str(payload.get("voice") or ctx.get("voice") or "")
    rate = str(payload.get("rate") or ctx.get("rate") or "-5%")
    pitch = payload.get("pitch") or ctx.get("pitch")
    app_dir = Path(ctx.get("app_dir") or ".")
    output_dir = app_dir / "output" / event.project_id
    output_dir.mkdir(parents=True, exist_ok=True)

    tts_files: list[Any] = []

    if segments and not ctx.get("subtitles_only"):
        from engines.tts import DEFAULT_VOICE, generate_audio

        def _synth_all() -> list[Any]:
            files: list[Any] = []
            for i, text in enumerate(segments):
                if not str(text or "").strip():
                    continue
                batch_files = generate_audio(
                    str(text),
                    voice=voice or DEFAULT_VOICE,
                    rate=rate,
                    pitch=pitch,
                    engine_id=tts_engine,
                    output_dir=output_dir,
                    task_id=event.project_id,
                )
                files.extend(batch_files or [])
            return files

        tts_files = await asyncio.to_thread(_synth_all)

    ctx["tts_files"] = tts_files
    await bus.publish(
        BusEvent.create(
            EventType.VOICE_COMPLETED,
            project_id=event.project_id,
            chunk_id=event.chunk_id,
            payload={
                "segments": segments,
                "timing_map": timing_map,
                "tts_files": tts_files,
            },
            source_agent="voice",
        )
    )


async def mix_handler(event: BusEvent, bus: AsyncEventBus, ctx: dict[str, Any]) -> None:
    if event.event_type not in (
        EventType.VOICE_COMPLETED.value,
        EventType.MIX_REQUESTED.value,
    ):
        return

    if ctx.get("skip_mix"):
        await bus.publish(
            BusEvent.create(
                EventType.MIX_COMPLETED,
                project_id=event.project_id,
                chunk_id=event.chunk_id,
                payload={"skipped": True},
                source_agent="mix",
            )
        )
        return

    force = bool(event.payload.get("force", True))

    def _mix() -> tuple[bool, str | None, list[str]]:
        from api.studio_api import run_studio_mix_internal

        return run_studio_mix_internal(event.project_id, force=force)

    ok, out_file, errors = await asyncio.to_thread(_mix)
    ctx["mix_ok"] = ok
    ctx["mix_output"] = out_file
    ctx["mix_errors"] = errors
    await bus.publish(
        BusEvent.create(
            EventType.MIX_COMPLETED,
            project_id=event.project_id,
            chunk_id=event.chunk_id,
            payload={"ok": ok, "output_file": out_file, "errors": errors},
            source_agent="mix",
        )
    )


async def export_handler(event: BusEvent, bus: AsyncEventBus, ctx: dict[str, Any]) -> None:
    if event.event_type not in (
        EventType.MIX_COMPLETED.value,
        EventType.EXPORT_REQUESTED.value,
    ):
        return

    output_path = str(
        event.payload.get("output_path") or ctx.get("export_path") or ""
    )
    timed_path = str(
        event.payload.get("timed_audio_path") or ctx.get("timed_audio_path") or ""
    )
    audio_obj = ctx.get("timed_audio_obj")

    export_ok = False
    if audio_obj is not None and output_path:
        from api.auto_dub_api import _safe_export_audio

        export_ok = await asyncio.to_thread(_safe_export_audio, audio_obj, output_path)
    elif timed_path and Path(timed_path).exists():
        export_ok = True

    ctx["export_ok"] = export_ok
    ctx["export_path"] = output_path
    await bus.publish(
        BusEvent.create(
            EventType.EXPORT_COMPLETED,
            project_id=event.project_id,
            chunk_id=event.chunk_id,
            payload={"ok": export_ok, "path": output_path},
            source_agent="export",
        )
    )
    await bus.publish(
        BusEvent.create(
            EventType.PIPELINE_COMPLETED,
            project_id=event.project_id,
            chunk_id=event.chunk_id,
            payload={"export_ok": export_ok},
            source_agent="export",
        )
    )


def start_all_agents(
    bus: AsyncEventBus,
    ctx: dict[str, Any],
) -> list[asyncio.Task]:
    """Launch all pipeline agents as concurrent asyncio tasks (TZ §5)."""

    async def _wrap(name: str, handler_fn: Callable) -> None:
        async def _h(event: BusEvent) -> None:
            await handler_fn(event, bus, ctx)

        await run_agent_loop(bus, name, _h)

    agents: list[tuple[str, Callable]] = [
        ("translator", translator_handler),
        ("cleaner", cleaner_handler),
        ("timing", timing_handler),
        ("voice", voice_handler),
        ("mix", mix_handler),
        ("export", export_handler),
    ]
    tasks = []
    for name, handler_fn in agents:
        task = asyncio.create_task(_wrap(name, handler_fn), name=f"agent-{name}")
        tasks.append(task)
    return tasks
