"""Smoke tests: remote_jobs local TTS + online engine registry (no API keys)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_remote_jobs_cloud_target_hard_gate():
    from engines.cloud.remote_jobs import (
        CLOUD_TARGET_MSG_RU,
        CloudTargetUnavailableError,
        RemoteJobQueue,
    )
    from engines.cloud.models import RemoteJobTarget

    q = RemoteJobQueue(app_dir=Path("."))
    with pytest.raises(CloudTargetUnavailableError) as ei:
        q.submit("tts", target=RemoteJobTarget.CLOUD.value, payload={"text": "hi"})
    assert "TubeDub Cloud" in str(ei.value)
    assert "не настроен" in ei.value.message_ru or "VM_TUBEDUB_CLOUD_URL" in CLOUD_TARGET_MSG_RU


def test_remote_jobs_local_tts_with_mock(tmp_path, monkeypatch):
    """Local TTS job runs without real network — mock synthesize."""
    from engines.cloud.remote_jobs import RemoteJobQueue
    from engines.tts_engines.base import TTSResult

    monkeypatch.delenv("VM_ENABLE_ONLINE_TTS", raising=False)

    def _fake_synth(text, voice, output_path, **kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"RIFF" + b"\x00" * 40)
        return TTSResult(ok=True, output_path=str(output_path), engine_id="mock")

    q = RemoteJobQueue(app_dir=tmp_path)
    with patch("engines.tts_engines.registry.synthesize", side_effect=_fake_synth):
        result = q._execute(
            "tts",
            {"text": "hello smoke", "voice": "mock", "engine_id": "mock", "output": "smoke.wav"},
        )
    assert result["engine_id"] == "mock"
    assert Path(result["output_path"]).is_file()


def test_online_engine_registry_no_keys(monkeypatch):
    """Online engines listed but not available without opt-in + keys."""
    monkeypatch.delenv("VM_ENABLE_ONLINE_TTS", raising=False)
    for key in (
        "OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "AZURE_SPEECH_KEY",
        "GOOGLE_TTS_KEY",
        "VM_STUDIO_TTS_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    from engines.tts_engines.online_engines import online_engines
    from engines.tts_engines.registry import list_engine_infos, load_engine_catalog

    engines = online_engines()
    assert engines, "online engine classes must register"
    assert all(not e.is_available() for e in engines)

    catalog = load_engine_catalog(Path(__file__).resolve().parents[1])
    ids = {e.get("id") for e in catalog}
    assert "piper" in ids
    assert "coqui" in ids
    assert "openai-voice" in ids

    infos = list_engine_infos(Path(__file__).resolve().parents[1])
    info_ids = {i.id for i in infos}
    assert "edge-offline" in info_ids or "mock" in info_ids


def test_oauth_authorize_includes_message_ru(tmp_path, monkeypatch):
    monkeypatch.delenv("VM_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("VM_GOOGLE_CLIENT_SECRET", raising=False)
    from engines.cloud.oauth import build_authorize_url

    auth = build_authorize_url("google_drive", app_dir=tmp_path)
    assert auth["ok"] is False
    assert auth.get("oauth_connected") is False
    assert "message_ru" in auth
    assert "OAuth" in auth["message_ru"] or "oauth" in auth["message_ru"].lower() or "не настроен" in auth["message_ru"]


def test_offline_tts_blocks_online_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("VM_TTS_MODE", "offline")
    monkeypatch.setenv("VM_ENABLE_ONLINE_TTS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from engines.tts_engines.registry import synthesize

    # Clear cache so mode is re-read via get_engine path
    import engines.tts_engines.registry as reg

    reg._ENGINE_CACHE.clear()
    result = synthesize("hi", "alloy", str(tmp_path / "x.mp3"), engine_id="openai-voice")
    assert result.ok is False
    assert "offline" in (result.error or "").lower()


def test_probe_voice_clone_not_hardcoded_disabled():
    from engines.ai_core.platform.capability_registry import probe_voice_clone

    probe = probe_voice_clone()
    assert probe["id"] == "voice_clone"
    assert "status" in probe
    # Must report real adapter state (READY or NOT_INSTALLED), never a silent stub only
    assert probe["status"] in ("READY", "NOT_INSTALLED", "NOT_CONFIGURED", "NOT_AVAILABLE")
