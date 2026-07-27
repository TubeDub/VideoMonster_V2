# -*- coding: utf-8 -*-
"""Stage 1 Happy Path: advanced shorteners OFF by default; Simple always Happy Path."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.happy_path import (
    USE_ADVANCED_ADAPTATION,
    advanced_adaptation_enabled,
    is_simple_mode,
    skip_advanced_text_shorteners,
    stamp_happy_path_meta,
)


def test_default_constant_is_false():
    assert USE_ADVANCED_ADAPTATION is False


def test_simple_mode_always_happy_path(monkeypatch):
    monkeypatch.setenv("USE_ADVANCED_ADAPTATION", "1")
    monkeypatch.setenv("VM_USE_ADVANCED_ADAPTATION", "1")
    assert advanced_adaptation_enabled({"user_mode": "basic"}) is False
    assert advanced_adaptation_enabled({}, user_mode="simple") is False
    assert is_simple_mode({"user_mode": "basic"}) is True
    assert skip_advanced_text_shorteners({"user_mode": "basic"}) is True


def test_pro_can_enable_via_env(monkeypatch):
    monkeypatch.setenv("USE_ADVANCED_ADAPTATION", "1")
    assert advanced_adaptation_enabled({"user_mode": "pro"}) is True
    monkeypatch.setenv("USE_ADVANCED_ADAPTATION", "0")
    assert advanced_adaptation_enabled({"user_mode": "pro"}) is False


def test_stamp_meta_labels_happy_path():
    info: dict = {"user_mode": "basic"}
    meta = stamp_happy_path_meta(info)
    assert meta["happy_path"] is True
    assert meta["adaptation_path"] == "happy_path"
    assert meta["USE_ADVANCED_ADAPTATION"] is False
    assert meta["adaptation_shorteners"] == ["naturalizer", "soft_compress"]


def test_stamp_meta_advanced_when_pro_env(monkeypatch):
    monkeypatch.setenv("VM_USE_ADVANCED_ADAPTATION", "1")
    info: dict = {"user_mode": "pro"}
    meta = stamp_happy_path_meta(info)
    assert meta["happy_path"] is False
    assert meta["adaptation_path"] == "advanced"
    assert "sso" in meta["adaptation_shorteners"]


def test_closed_loop_allows_zero_max_iterations():
    """Happy Path pause-only: max_iterations=0 must not be forced to 1."""
    from engines.closed_loop_timing import run_closed_loop_timing

    # Empty segments — just verify the clamp accepts 0 without raising.
    stats = run_closed_loop_timing(
        [],
        [],
        source_segments=[],
        voice="x",
        target_lang="uk",
        src_lang="en",
        work_dir=ROOT / "temp",
        regen_fn=None,
        max_iterations=0,
    )
    assert isinstance(stats, dict)
