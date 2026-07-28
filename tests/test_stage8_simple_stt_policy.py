# -*- coding: utf-8 -*-
"""Stage 8 — Simple STT policy (small + beam=1, no medium/large)."""

from __future__ import annotations

from engines.simple_stt_policy import (
    apply_simple_stt_policy,
    resolve_simple_stt_beam,
    resolve_simple_stt_model,
    should_force_simple_stt,
)


def test_resolve_caps_medium_and_large():
    assert resolve_simple_stt_model("medium") == "small"
    assert resolve_simple_stt_model("large") == "small"
    assert resolve_simple_stt_model("small") == "small"
    assert resolve_simple_stt_model("tiny") == "tiny"
    assert resolve_simple_stt_model(None) == "small"


def test_beam_default_one():
    assert resolve_simple_stt_beam(model_size="small") == 1


def test_should_force_simple():
    assert should_force_simple_stt({"user_mode": "basic"})
    assert should_force_simple_stt({"happy_path": True})
    assert should_force_simple_stt({"simple_pipeline": True})
    assert not should_force_simple_stt(
        {
            "user_mode": "pro",
            "happy_path": False,
            "simple_pipeline": False,
        }
    )


def test_apply_stamps_lock_and_knobs(monkeypatch):
    monkeypatch.setattr(
        "engines.simple_stt_policy.resolve_simple_stt_device",
        lambda: ("cpu", "int8"),
    )
    info = apply_simple_stt_policy({"user_mode": "basic"}, requested_model="medium")
    assert info["stt_model"] == "small"
    assert info["model_size"] == "small"
    assert info["stt_beam_size"] == 1
    assert info["stt_vad_filter"] is True
    assert info["stt_word_timestamps"] is False
    assert info["simple_stt_locked"] is True
    assert info["voice_verification_asr_allowed"] is False
    assert info["post_tts_restt_allowed"] is False
    assert info["stt_device"] == "cpu"
    assert info["stt_compute_type"] == "int8"
