"""Developer Preview + diagnostic archive fixes."""

from __future__ import annotations


def test_resolve_restart_cache_plan_voice_only():
    from engines.developer_preview import resolve_restart_cache_plan

    info = {
        "target_lang": "uk",
        "voice": "uk-UA-OstapNeural",
        "dub_style": "modern",
        "translation_agent_path": True,
    }
    plan = resolve_restart_cache_plan(
        info,
        {"voice": "uk-UA-PolinaNeural"},
        checkpoint="post_ai_core_text",
    )
    assert plan["skip_translate"] is True
    assert plan["reason"] == "voice_only"


def test_resolve_restart_cache_plan_language_change():
    from engines.developer_preview import resolve_restart_cache_plan

    info = {"target_lang": "uk", "voice": "uk-UA-OstapNeural"}
    plan = resolve_restart_cache_plan(
        info,
        {"target_lang": "ru"},
        checkpoint="post_ai_core_text",
    )
    assert plan["skip_translate"] is False
    assert plan["reason"] == "language_changed"


def test_contiguous_ready_prefix():
    from engines.developer_preview import contiguous_ready_prefix, count_tts_ready_segments

    segs = [
        {"file": "a.mp3"},
        {"file": "b.mp3"},
        {"text": "no audio"},
        {"file": "d.mp3"},
    ]
    assert contiguous_ready_prefix(segs) == 1
    assert count_tts_ready_segments(segs) == 3


def test_build_agent_timeline_view():
    from engines.developer_preview import build_agent_timeline_view, record_agent_event

    info: dict = {}
    record_agent_event(info, "whisper", "running")
    record_agent_event(info, "whisper", "done", duration_ms=1200)
    record_agent_event(info, "translation", "running")
    rows = build_agent_timeline_view(info, current_step="translate")
    by_agent = {r["agent"]: r["status"] for r in rows}
    assert by_agent["whisper"] == "done"
    assert by_agent["translation"] in ("running", "pending")


def test_ensure_diagnostic_archive_uses_qa_bundle(tmp_path):
    from engines.pipeline_integrity.passive_openddf import (
        ensure_diagnostic_archive,
        start_diagnostic_run,
    )

    start_diagnostic_run("qa-archive", output_dir=tmp_path)
    task_info = {
        "segments_data": [{"index": 0, "text": "Hello", "file": "s0.mp3"}],
        "translation_audits": [{"index": 0, "final_text": "Привіт"}],
        "post_tts_qa": {"checked": 1},
    }
    path = ensure_diagnostic_archive("qa-archive", task_info=task_info, output_dir=tmp_path)
    assert path
    assert "ensure_archive" in path or "final_qa" in path or path.endswith(".zip")
