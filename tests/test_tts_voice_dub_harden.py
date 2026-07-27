"""Production hardening: TTS handoff gate, voice API, remux defaults, stubs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def app_ctx():
    from app import app

    with app.app_context():
        yield app


def test_pipeline_tts_group_timeout_aligned_with_segment():
    from engines import tts as tts_mod

    assert tts_mod.PIPELINE_TTS_GROUP_TIMEOUT >= float(tts_mod.TTS_SEGMENT_TIMEOUT)


def test_online_engines_unavailable_without_opt_in(monkeypatch):
    monkeypatch.delenv("VM_ENABLE_ONLINE_TTS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from engines.tts_engines.online_engines import online_engines

    engines = online_engines()
    assert engines
    assert all(not e.is_available() for e in engines)


def test_online_engines_opt_in_requires_key(monkeypatch):
    monkeypatch.setenv("VM_ENABLE_ONLINE_TTS", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VM_OPENAI_API_KEY", raising=False)
    from engines.tts_engines.online_engines import OpenAIVoiceEngine

    eng = OpenAIVoiceEngine()
    assert eng.is_available() is False
    result = eng.synthesize("hi", "alloy", "out.mp3")
    assert result.ok is False
    assert "not available" in (result.error or "").lower() or "VM_ENABLE_ONLINE_TTS" in (
        result.error or ""
    )


def test_online_engines_opt_in_with_key(monkeypatch):
    monkeypatch.setenv("VM_ENABLE_ONLINE_TTS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from engines.tts_engines.online_engines import OpenAIVoiceEngine

    assert OpenAIVoiceEngine().is_available() is True


def test_segment_tts_voice_prefers_assigned():
    from api.auto_dub_api import _segment_tts_voice

    seg = {
        "assigned_voice": "uk-UA-OstapNeural",
        "voice": "ru-RU-DmitryNeural",
        "ai_voice": {"voice": "en-US-GuyNeural"},
    }
    assert _segment_tts_voice(seg, "fallback") == "uk-UA-OstapNeural"
    assert _segment_tts_voice({}, "fallback") == "fallback"


def test_apply_voice_platform_assignments_sets_voices(tmp_path, monkeypatch):
    monkeypatch.setattr("api.auto_dub_api.OUTPUT_DIR", tmp_path)
    import threading

    monkeypatch.setattr("api.auto_dub_api.STATE_LOCK", threading.RLock())
    from engines.dub_task_state import AUTO_TASKS

    AUTO_TASKS["t1"] = {"info": {}}
    from api.auto_dub_api import _apply_voice_platform_assignments

    segments = [
        {"segment_id": "s1", "speaker": "A", "text": "Привіт"},
        {"segment_id": "s2", "speaker": "B", "text": "Hello"},
        {"segment_id": "s3", "speaker": "A", "text": "Знову"},
    ]
    out = _apply_voice_platform_assignments(
        "t1",
        segments,
        default_voice="uk-UA-OstapNeural",
        target_lang="uk",
        style_id="Movie",
        preferred_voices={
            "A": "uk-UA-OstapNeural",
            "B": "uk-UA-PolinaNeural",
        },
    )
    assert out == "uk-UA-OstapNeural"
    assert segments[0].get("assigned_voice") == "uk-UA-OstapNeural"
    assert segments[2].get("assigned_voice") == "uk-UA-OstapNeural"
    assert segments[1].get("assigned_voice") == "uk-UA-PolinaNeural"


def test_remux_default_mix_mode_is_full_dub():
    src = Path("api/auto_dub_api.py").read_text(encoding="utf-8")
    assert 'mix_mode=info.get("mix_mode_backup") or "full_dub"' in src
    assert 'or "standard"' not in src.replace(
        'mix_mode=info.get("mix_mode_backup") or "full_dub"', ""
    )


def test_no_silent_fallback_in_export_path():
    src = Path("api/auto_dub_api.py").read_text(encoding="utf-8")
    assert "silent_segment = AudioSegment.silent" not in src
    assert "TTS_HANDOFF_EMPTY" in src


def test_no_hardcoded_debug_ee98_log():
    src = Path("api/auto_dub_api.py").read_text(encoding="utf-8")
    assert "debug-ee98a6.log" not in src


def test_dub_check_uses_find_ffmpeg(app_ctx):
    from api import dub_api

    with patch("engines.ffmpeg_paths.find_ffmpeg", return_value=r"C:\app\ffmpeg\ffmpeg.exe"), patch(
        "engines.ffmpeg_paths.find_ffprobe", return_value=r"C:\app\ffmpeg\ffprobe.exe"
    ):
        resp = dub_api.api_dub_check()
        data = resp.get_json()
    assert data["ffmpeg"] is True
    assert "ffmpeg.exe" in data["ffmpeg_path"]


def test_voice_clone_status_reports_null_adapter(app_ctx):
    from api import voice_api

    with patch("engines.voice_platform.cloning.get_clone_adapter") as mock_get:
        adapter = MagicMock()
        adapter.is_available.return_value = False
        adapter.adapter_id = "clone-null"
        mock_get.return_value = adapter
        resp = voice_api.api_voice_clone_status()
        data = resp.get_json()
    assert data["ok"] is True
    assert data["available"] is False
    assert data["adapter_id"] == "clone-null"


def test_tts_api_timing_mode_helpers():
    from api import tts_api

    assert tts_api._parse_total_duration("01:02:03") == 3723.0
    assert tts_api._parse_total_duration("2:30") == 150.0
    assert tts_api._parse_total_duration("bad") == 0.0
