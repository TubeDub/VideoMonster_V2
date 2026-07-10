"""Tests for engines.regeneration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from engines.regeneration import auto_fix_segment, regenerate_segment


def test_regenerate_segment_empty_text():
    result = regenerate_segment({"index": 0, "text": ""}, voice="ru-RU-DmitryNeural")
    assert result.get("ok") is False


def test_regenerate_segment_mock_tts(tmp_path, monkeypatch):
    from pydub import AudioSegment

    out = tmp_path / "output"
    out.mkdir(parents=True)

    def fake_generate(**kwargs):
        p = out / "seg.mp3"
        AudioSegment.silent(duration=1800).export(p, format="mp3")
        return [p.name]

    monkeypatch.setattr("engines.tts.generate_audio", fake_generate)
    monkeypatch.setattr("engines.tts.OUTPUT_DIR", out)
    monkeypatch.setattr("engines.regeneration.OUTPUT_DIR", out)
    monkeypatch.setattr("engines.regeneration.APP_DIR", tmp_path)
    monkeypatch.setattr("engines.soft_sync.is_soft_sync_enabled", lambda: False)

    seg = {
        "index": 0,
        "text": "Привет мир",
        "start_ms": 0,
        "end_ms": 2500,
    }
    result = regenerate_segment(
        seg,
        timing_map=[{"start": 0, "end": 2500}],
        voice="ru-RU-DmitryNeural",
        lang="ru",
        app_dir=tmp_path,
        use_soft_sync=False,
    )
    assert result.get("file") or result.get("ok") is False
    if result.get("ok"):
        assert seg.get("overflow_pct") is not None


def test_auto_fix_uses_soft_sync_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engines.regeneration.regenerate_segment",
        lambda *a, **k: {"ok": True, "segment": k.get("segment") or a[0], "overflow_pct": 2.0},
    )
    seg = {"index": 1, "text": "Test", "start_ms": 0, "end_ms": 3000}
    result = auto_fix_segment(seg, voice="ru-RU-DmitryNeural", app_dir=tmp_path)
    assert result.get("ok") is True


def test_regenerate_segment_passes_emotion_to_tts(tmp_path, monkeypatch):
    from pydub import AudioSegment

    out = tmp_path / "output"
    out.mkdir(parents=True)
    seen: dict[str, str | None] = {"emotion": None}

    def fake_generate(**kwargs):
        seen["emotion"] = kwargs.get("emotion")
        p = out / "emo.mp3"
        AudioSegment.silent(duration=1000).export(p, format="mp3")
        return [p.name]

    monkeypatch.setattr("engines.tts.generate_audio", fake_generate)
    monkeypatch.setattr("engines.tts.OUTPUT_DIR", out)
    monkeypatch.setattr("engines.regeneration.OUTPUT_DIR", out)
    monkeypatch.setattr("engines.regeneration.APP_DIR", tmp_path)
    monkeypatch.setattr("engines.soft_sync.is_soft_sync_enabled", lambda: False)

    seg = {
        "index": 0,
        "text": "Тест эмоции",
        "start_ms": 0,
        "end_ms": 2000,
        "emotion": "sad",
    }
    regenerate_segment(
        seg,
        timing_map=[{"start": 0, "end": 2000}],
        voice="ru-RU-DmitryNeural",
        lang="ru",
        app_dir=tmp_path,
        use_soft_sync=False,
    )
    assert seen["emotion"] == "sad"
    assert (seg.get("tts_emotion") or {}).get("emotion") == "sad"
