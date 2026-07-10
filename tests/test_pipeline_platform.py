"""Tests for TubeDub Pipeline Platform (mandatory TZ architecture)."""

from __future__ import annotations


def test_platform_stages_registered():
    from engines.pipeline_platform import bootstrap_stages, list_stages
    from engines.pipeline_platform.contract import StageId

    bootstrap_stages()
    stages = list_stages()
    ids = {s["stage_id"] for s in stages}
    for sid in StageId:
        assert sid.value in ids


def test_run_segment_trace_minimal():
    from engines.pipeline_platform.orchestrator import build_context_from_info, run_segment_trace

    info = {
        "segments_data": [{"index": 0, "text": "Hello world", "source_text": "Hello world"}],
        "source_lang": "en",
        "target_lang": "uk",
        "translation_audits": [
            {
                "index": 0,
                "source_text": "Hello world",
                "raw_translation": "Привіт світ",
                "final_text": "Привіт, світ",
            }
        ],
    }
    ctx = build_context_from_info(info)
    trace = run_segment_trace(ctx, 0)
    assert trace.segment_index == 0
    assert len(trace.stages) >= 9
    assert trace.stages[0].stage_id == "stt"


def test_word_timing_merge():
    from engines.pipeline_platform.word_timing_bridge import merge_word_timings_on_fewer_words

    words = [
        {"word": "a", "start_ms": 0, "end_ms": 100, "confidence": 0.9, "position": 0},
        {"word": "b", "start_ms": 100, "end_ms": 200, "confidence": 0.8, "position": 1},
        {"word": "c", "start_ms": 200, "end_ms": 400, "confidence": 0.7, "position": 2},
    ]
    merged = merge_word_timings_on_fewer_words(words, 2)
    assert len(merged) == 2
    assert merged[0]["start_ms"] == 0
    assert merged[1]["end_ms"] == 400


def test_translation_optimizer_no_meaning_change_short():
    from engines.pipeline_platform.translation_optimizer_platform import optimize_translation_text

    r = optimize_translation_text("Hi", slot_ms=5000, src_lang="en", tgt_lang="uk")
    assert r.optimized == "Hi"
    assert not r.changed


def test_timing_engine_start_immutable():
    from engines.pipeline_platform.timing_engine import run_timing_engine

    wtm = {
        "segment_start_ms": 1000,
        "segment_end_ms": 3000,
        "words": [{"text": "test", "start_ms": 1000, "end_ms": 2000, "confidence": 1.0}],
    }
    out = run_timing_engine(text="short", slot_ms=2000, word_timing_map=wtm, tgt_lang="uk")
    assert out["start_ms"] == 1000


def test_dev_pipeline_view_structure():
    from engines.pipeline_platform.dev_view import build_dev_pipeline_view

    info = {
        "segments_data": [{"index": 0, "text": "Test", "source_text": "Test"}],
        "translation_audits": [{"index": 0, "source_text": "Test", "final_text": "Тест"}],
        "source_lang": "en",
        "target_lang": "uk",
    }
    view = build_dev_pipeline_view(info)
    assert view["segment_count"] == 1
    chain = view["segments"][0]["chain"]
    labels = [c["label"] for c in chain]
    assert "Original" in labels
    assert "STT" in labels
    assert "Mux" in labels
    assert view.get("copy_text")


def test_plugin_host_list():
    from engines.dub_studio.plugin_host import list_all_plugins

    plugins = list_all_plugins()
    assert any(p.get("plugin_id") == "eq" for p in plugins)
