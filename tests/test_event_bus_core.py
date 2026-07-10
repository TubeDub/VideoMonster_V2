"""Tests for Event Bus Core (TZ Stage 1)."""

from __future__ import annotations

import asyncio

import pytest

from core.event_bus import AsyncEventBus, reset_event_bus
from core.event_types import BusEvent, EventType


def test_bus_event_requires_typed_envelope():
    with pytest.raises(ValueError):
        BusEvent.create("", project_id="p1")
    ev = BusEvent.create(
        EventType.TRANSLATION_REQUESTED,
        project_id="task-1",
        chunk_id=3,
        payload={"segments": ["hello"]},
    )
    assert ev.event_type == "translation_requested"
    assert ev.chunk_id == 3
    assert ev.project_id == "task-1"


def test_publish_subscribe_unsubscribe():
    async def _run():
        bus = AsyncEventBus()
        sub = bus.subscribe(EventType.SEGMENTS_ALIGNED.value, agent_name="timing")
        ev = BusEvent.create(
            EventType.SEGMENTS_ALIGNED,
            project_id="t1",
            chunk_id=1,
            payload={"segments": ["a"]},
            source_agent="cleaner",
        )
        n = await bus.publish(ev)
        assert n >= 1
        received = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert received.event_type == EventType.SEGMENTS_ALIGNED.value
        assert bus.unsubscribe(sub.subscription_id)
        n2 = await bus.broadcast(ev)
        assert n2 == 0

    asyncio.run(_run())


def test_agent_error_does_not_stop_bus():
    async def _run():
        bus = reset_event_bus()
        sub = bus.subscribe(EventType.AGENT_ERROR.value, agent_name="monitor")
        err = BusEvent.create(
            EventType.AGENT_ERROR,
            project_id="t1",
            chunk_id=0,
            payload={"agent": "voice", "error": "test", "recoverable": True},
            source_agent="voice",
        )
        await bus.publish(err)
        got = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert got.payload["agent"] == "voice"
        assert bus.running is True

    asyncio.run(_run())


def test_translation_chain_mocked(monkeypatch):
    """Translator → Cleaner chain via bus without real LLM/MT."""

    async def _run():
        from core.event_pipeline import PipelineRunConfig, _AGENT_HANDLERS, run_pipeline_async

        async def fake_translator(event, bus, ctx):
            ctx["translated_segments"] = ["translated"]
            await bus.publish(
                BusEvent.create(
                    EventType.TRANSLATION_COMPLETED,
                    project_id=event.project_id,
                    chunk_id=event.chunk_id,
                    payload={"segments": ["translated"], "timing_map": ctx["timing_map"]},
                    source_agent="translator",
                )
            )

        async def fake_cleaner(event, bus, ctx):
            ctx["segments"] = ["aligned"]
            await bus.publish(
                BusEvent.create(
                    EventType.SEGMENTS_ALIGNED,
                    project_id=event.project_id,
                    chunk_id=event.chunk_id,
                    payload={"segments": ["aligned"]},
                    source_agent="cleaner",
                )
            )

        monkeypatch.setitem(_AGENT_HANDLERS, "translator", fake_translator)
        monkeypatch.setitem(_AGENT_HANDLERS, "cleaner", fake_cleaner)

        result = await run_pipeline_async(
            PipelineRunConfig(
                project_id="test-task",
                source_segments=["hello"],
                timing_map=[{"start": 0, "end": 1000}],
                agents=("translator", "cleaner"),
                timeout_sec=10.0,
            )
        )
        assert result.ok
        assert result.segments == ["aligned"]

    asyncio.run(_run())


def test_full_six_agent_chain(monkeypatch):
    """All 6 agents run through the bus end-to-end (acceptance §criteria)."""

    class _FakePipe:
        def __init__(self, *a, **k):
            class _QL:
                def records_as_dicts(self_inner):
                    return []

            self.quality_log = _QL()

        def translate_segments(self, src, tm, sl, tl, translate_meta_out=None, progress_cb=None):
            class _R:
                segments = ["перевод"]
                meta: dict = {}
            return _R()

        def flush_quality_log(self, **k):
            pass

    monkeypatch.setattr(
        "engines.translation_pipeline.UniversalTranslationPipeline", _FakePipe
    )
    monkeypatch.setattr(
        "engines.cleaner.align_segments_to_timing_map",
        lambda segs, tm: [str(s) for s in segs] or ["перевод"],
    )
    monkeypatch.setattr(
        "engines.cleaner.split_by_timing_map", lambda block, tm: [block]
    )
    monkeypatch.setattr(
        "engines.timing_aware_translation.adapt_segments_to_timing",
        lambda *a, **k: (list(a[0]), []),
    )
    monkeypatch.setattr("engines.tts.generate_audio", lambda *a, **k: ["seg_0000.mp3"])
    monkeypatch.setattr("engines.tts.DEFAULT_VOICE", "ru-RU-Test")
    monkeypatch.setattr(
        "api.studio_api.run_studio_mix_internal",
        lambda tid, force=True: (True, "out.mp4", []),
    )

    async def _run():
        from core.event_pipeline import PipelineRunConfig, run_pipeline_async

        result = await run_pipeline_async(
            PipelineRunConfig(
                project_id="full-task",
                source_segments=["hello"],
                timing_map=[{"start": 0, "end": 1000}],
                agents=("translator", "cleaner", "timing", "voice", "mix", "export"),
                skip_mix=False,
                timeout_sec=20.0,
            )
        )
        assert result.ok, result.errors
        assert result.tts_files == ["seg_0000.mp3"]
        types = [e["event_type"] for e in result.bus_history]
        for expected in (
            "translation_completed",
            "segments_aligned",
            "timing_completed",
            "voice_completed",
            "mix_completed",
            "export_completed",
            "pipeline_completed",
        ):
            assert expected in types, f"missing {expected} in {types}"

    asyncio.run(_run())


def test_agent_exception_isolated_from_chain(monkeypatch):
    """A failing agent publishes agent_error without stopping the bus (§7)."""

    async def _run():
        from core.event_pipeline import PipelineRunConfig, _AGENT_HANDLERS, run_pipeline_async

        async def boom_translator(event, bus, ctx):
            raise RuntimeError("translator boom")

        monkeypatch.setitem(_AGENT_HANDLERS, "translator", boom_translator)

        result = await run_pipeline_async(
            PipelineRunConfig(
                project_id="err-task",
                source_segments=["hi"],
                timing_map=[{"start": 0, "end": 500}],
                agents=("translator", "cleaner"),
                timeout_sec=3.0,
            )
        )
        # Chain never completes but bus captured the error and stayed alive.
        assert result.ok is False
        assert any("translator" in e for e in result.errors)

    asyncio.run(_run())
