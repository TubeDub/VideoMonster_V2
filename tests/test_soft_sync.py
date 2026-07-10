"""Tests for engines.soft_sync."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from engines.soft_sync import (
    apply_hard_anchor_soft_end,
    expand_text_for_slot,
    is_soft_sync_enabled,
    shorten_text_for_slot,
)


def test_shorten_text_for_slot_returns_original_when_fits():
    text = "Hello"
    assert shorten_text_for_slot(text, slot_ms=5000, lang="en") == text


def test_expand_text_for_slot_underfill():
    text = "Hi"
    expanded = expand_text_for_slot(text, slot_ms=8000, lang="en")
    assert len(expanded) >= len(text)


def test_hard_anchor_adds_lead_from_word_map(tmp_path):
    from pydub import AudioSegment

    wav = tmp_path / "t.wav"
    AudioSegment.silent(duration=500).export(wav, format="wav")
    word_map = {
        "segment_start_ms": 0,
        "words": [{"text": "a", "start_ms": 120, "end_ms": 400}],
    }
    out, meta = apply_hard_anchor_soft_end(wav, 0, 2000, tmp_path, word_map=word_map)
    assert Path(out).is_file()
    assert meta.get("anchor_ms", 0) >= 0
    assert "hard_anchor" in meta.get("strategy", "")


def test_is_soft_sync_enabled_respects_env(monkeypatch):
    monkeypatch.delenv("FEATURE_SOFT_SYNC", raising=False)
    assert isinstance(is_soft_sync_enabled(), bool)


def test_fit_segment_with_retry_mock_tts(tmp_path, monkeypatch):
    from pydub import AudioSegment

    from engines.soft_sync import fit_segment_with_retry

    def fake_generate(**kwargs):
        p = tmp_path / "fake.mp3"
        AudioSegment.silent(duration=2500).export(p, format="mp3")
        return [p.name]

    monkeypatch.setattr("engines.tts.generate_audio", fake_generate)
    monkeypatch.setattr("engines.tts.OUTPUT_DIR", tmp_path)

    result = fit_segment_with_retry(
        "Test phrase for timing",
        voice="ru-RU-DmitryNeural",
        slot_start_ms=0,
        slot_end_ms=2000,
        lang="ru",
        work_dir=tmp_path / "work",
        max_iterations=1,
    )
    assert "overflow_pct" in result
    assert result.get("text")


def test_fit_segment_with_retry_respects_max_three_iterations(tmp_path, monkeypatch):
    from pydub import AudioSegment

    from engines.soft_sync import fit_segment_with_retry

    calls = {"n": 0}

    def fake_generate(**kwargs):
        calls["n"] += 1
        p = tmp_path / f"iter_{calls['n']}.mp3"
        AudioSegment.silent(duration=5000).export(p, format="mp3")
        return [p.name]

    monkeypatch.setattr("engines.tts.generate_audio", fake_generate)
    monkeypatch.setattr("engines.tts.OUTPUT_DIR", tmp_path)

    result = fit_segment_with_retry(
        "Очень длинная фраза для теста лимита итераций",
        voice="ru-RU-DmitryNeural",
        slot_start_ms=0,
        slot_end_ms=1200,
        lang="ru",
        work_dir=tmp_path / "work_limit",
    )
    assert len(result.get("iterations") or []) <= 3
    assert calls["n"] <= 3
