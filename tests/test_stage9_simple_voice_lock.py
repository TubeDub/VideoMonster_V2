# -*- coding: utf-8 -*-
"""Stage 9 — Simple single-voice lock."""

from __future__ import annotations

from engines.simple_voice_lock import (
    collect_unique_voices,
    lock_simple_pipeline_voice,
    should_lock_simple_voice,
)


def test_should_lock_simple():
    assert should_lock_simple_voice({"user_mode": "basic"})
    assert should_lock_simple_voice({"happy_path": True})
    assert not should_lock_simple_voice(
        {"user_mode": "pro", "happy_path": False, "simple_pipeline": False}
    )


def test_lock_forces_one_voice():
    segs = [
        {"text": "a", "assigned_voice": "uk-UA-OstapNeural", "voice": "uk-UA-OstapNeural"},
        {"text": "b", "assigned_voice": "uk-UA-PolinaNeural", "voice": "uk-UA-PolinaNeural"},
        {"text": "c", "assigned_voice": "uk-UA-OstapNeural"},
    ]
    stamp = lock_simple_pipeline_voice(
        segs, pipeline_voice="uk-UA-OstapNeural", task_info={}
    )
    assert stamp["unique_voices_used"] == 1
    assert stamp["simple_voice_locked"] is True
    assert collect_unique_voices(segs) == ["uk-UA-OstapNeural"]
    assert all(s["assigned_voice"] == "uk-UA-OstapNeural" for s in segs)
    assert all(s["voice"] == "uk-UA-OstapNeural" for s in segs)
