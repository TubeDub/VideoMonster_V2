# -*- coding: utf-8 -*-
"""Stage 7b — Simple MT path lock (no Qwen / no translation agent)."""

from __future__ import annotations

from engines.simple_mt_path import (
    build_simple_mt_ui_timing,
    resolve_translate_method,
    stamp_simple_mt_lock,
    use_locked_simple_mt,
)


def test_use_locked_simple_mt_for_basic_and_happy():
    assert use_locked_simple_mt({"user_mode": "basic"})
    assert use_locked_simple_mt({"happy_path": True})
    assert use_locked_simple_mt({"simple_pipeline": True})
    assert not use_locked_simple_mt({"user_mode": "pro", "happy_path": False, "simple_pipeline": False, "USE_ADVANCED_ADAPTATION": True})


def test_resolve_translate_method():
    assert resolve_translate_method({"mt_engine": "marian_batch", "mt_calls": 1}) == "marian_batch"
    assert (
        resolve_translate_method(
            {"mt_engine": "cache", "mt_cache_hits": 5, "mt_cache_misses": 0, "mt_calls": 0}
        )
        == "mt_cache"
    )


def test_ui_timing_hides_qwen():
    ui = build_simple_mt_ui_timing(
        subphase="marian_mt", wall_sec=12.0, segments_done=4, segments_total=10
    )
    assert ui["phase_status"]["llm_adaptation"] == "skipped"
    assert "llm_adaptation" in ui["hidden_buckets"]
    assert ui["llm_adaptation_used"] is False
    assert ui["ui_labels"]["marian_mt"] == "Marian MT"

    ui2 = build_simple_mt_ui_timing(
        subphase="done", wall_sec=0.0, segments_done=10, segments_total=10, cache_mode=True
    )
    assert ui2["ui_labels"]["marian_mt"] == "Кэш перевода"


def test_stamp_lock_flags():
    info = {}
    stamp_simple_mt_lock(info)
    assert info["translation_agent_path"] is False
    assert info["llm_adaptation_used"] is False
    assert info["tps_skip_orchestrator"] is True
    assert info["simple_mt_locked"] is True
