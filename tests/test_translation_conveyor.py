"""Tests for translation conveyor foundation (TZ pipeline optimization)."""

from __future__ import annotations

from pathlib import Path

from engines.pipeline_orchestrator.marian_result_cache import get_cached, put_cached
from engines.pipeline_orchestrator.sliding_context import build_sliding_context
from engines.pipeline_orchestrator.stage_retry import run_with_retry
from engines.pipeline_orchestrator.translation_batch import (
    build_translation_batches,
    split_batch_translation,
    TranslationBatch,
)


def test_build_translation_batches_groups_short_segments():
    segments = ["Hello.", "How are you?", "Fine.", "Thanks.", "Bye."]
    batches = build_translation_batches(segments, min_tokens=1, max_tokens=100, min_sentences=1, max_sentences=3)
    assert len(batches) >= 1
    total = sum(len(b.segment_indices) for b in batches)
    assert total == len(segments)


def test_split_batch_translation_by_delimiter():
    batch = TranslationBatch(
        batch_id=0,
        segment_indices=[0, 1],
        source_texts=["Hello.", "World."],
        combined_source="Hello.\n\nWorld.",
    )
    out = split_batch_translation(batch, "Привіт.\n\nСвіт.")
    assert out[0] == "Привіт."
    assert out[1] == "Світ."


def test_sliding_context_prev_next():
    segs = ["A", "B", "C"]
    ctx = build_sliding_context(1, segs, window=1)
    assert ctx.previous == "A"
    assert ctx.current == "B"
    assert ctx.next_segment == "C"


def test_marian_cache_roundtrip(tmp_path):
    put_cached("hello", "en", "uk", "привіт", app_dir=tmp_path)
    assert get_cached("hello", "en", "uk", app_dir=tmp_path) == "привіт"
    assert get_cached("other", "en", "uk", app_dir=tmp_path) is None


def test_stage_retry_succeeds_after_transient_failure():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    result = run_with_retry(flaky, max_attempts=3, base_delay_s=0.01, stage="test")
    assert result.ok
    assert result.value == "ok"
    assert result.attempts == 2


def test_segment_conveyor_runs_stages():
    from engines.pipeline_orchestrator.segment_conveyor import (
        SegmentConveyor,
        SegmentConveyorConfig,
        SegmentWork,
    )

    order: list[str] = []

    def marian(item: SegmentWork) -> SegmentWork:
        item.raw_mt = item.source_text.upper()
        order.append(f"m{item.index}")
        return item

    def llm(item: SegmentWork) -> SegmentWork:
        item.polished = item.raw_mt + "!"
        order.append(f"l{item.index}")
        return item

    def tts(item: SegmentWork) -> SegmentWork:
        item.tts_file = f"f{item.index}.mp3"
        order.append(f"t{item.index}")
        return item

    cfg = SegmentConveyorConfig(
        marian_workers=2,
        llm_workers=2,
        tts_workers=2,
        marian_fn=marian,
        llm_fn=llm,
        tts_fn=tts,
    )
    items = [SegmentWork(i, source_text=f"s{i}") for i in range(4)]
    conv = SegmentConveyor(cfg, task_id="test-seg")
    out = conv.run(items)
    assert len(out) == 4
    assert all(o.tts_file for o in out)
    assert len(order) == 12


def test_full_conveyor_disabled_when_env_off(monkeypatch):
    monkeypatch.setenv("VM_PIPELINE_CONVEYOR", "0")
    from engines.pipeline_orchestrator.dub_conveyor_bridge import try_run_full_conveyor

    assert try_run_full_conveyor(
        task_id="t",
        source_segments=["a", "b"],
        timing_map=[],
        source_lang="en",
        target_lang="uk",
        voice="",
        app_dir=Path("."),
    ) is None
