# -*- coding: utf-8 -*-
"""Simple pipeline policy — one short path like pyVideoTrans."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_apply_simple_policy_locks_gates():
    from engines.simple_dub_pipeline import (
        SIMPLE_DISABLED,
        SIMPLE_PIPELINE_STEPS,
        apply_simple_pipeline_policy,
        should_auto_mix_mp4,
    )

    info = {"user_mode": "basic"}
    apply_simple_pipeline_policy(info)
    assert info["simple_pipeline"] is True
    assert info["happy_path"] is True
    assert info["USE_ADVANCED_ADAPTATION"] is False
    assert info["post_tts_resegment_allowed"] is False
    assert info["blind_timing_align_allowed"] is False
    assert info["text_fit_required"] is True
    assert float(info["max_atempo"]) <= 1.08
    assert float(info["min_atempo"]) >= 0.95
    assert info["simple_auto_mix"] is True
    assert should_auto_mix_mp4(info) is True
    assert "ada" in SIMPLE_DISABLED
    assert "text_fit_to_slot" in SIMPLE_PIPELINE_STEPS


def test_simple_ignores_advanced_env(monkeypatch):
    monkeypatch.setenv("USE_ADVANCED_ADAPTATION", "1")
    from engines.simple_dub_pipeline import apply_simple_pipeline_policy
    from engines.happy_path import advanced_adaptation_enabled

    info = {"user_mode": "basic"}
    apply_simple_pipeline_policy(info)
    assert advanced_adaptation_enabled(info) is False
    assert info["adaptation_path"] == "happy_path"


def test_stamp_happy_path_includes_resegment_gate():
    from engines.happy_path import stamp_happy_path_meta

    info = {"user_mode": "basic"}
    stamp_happy_path_meta(info)
    assert info["post_tts_resegment_allowed"] is False
    assert info["text_fit_required"] is True
