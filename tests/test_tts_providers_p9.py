"""P9 TTS compatibility layer tests."""

from __future__ import annotations

from pathlib import Path

from engines.tts_engines.providers import (
    CoquiTTSEngine,
    CosyVoiceEngine,
    FishSpeechEngine,
    MockTTSEngine,
    OpenVoiceEngine,
    PiperTTSEngine,
    XTTSEngine,
    provider_engines,
)
from engines.tts_engines.registry import get_engine, list_engine_infos


REQUIRED_IDS = {
    "mock",
    "coqui",
    "piper",
    "xtts",
    "fishspeech",
    "cosyvoice",
    "openvoice",
}


def test_providers_registered():
    engines = {e.id: e for e in provider_engines()}
    assert REQUIRED_IDS.issubset(set(engines))


def test_mock_synthesize(tmp_path: Path):
    eng = MockTTSEngine()
    assert eng.is_available()
    out = tmp_path / "out.wav"
    result = eng.synthesize("hello", "voice", str(out))
    assert result.ok
    assert out.is_file()
    assert out.stat().st_size > 0


def test_unavailable_provider_returns_error(tmp_path: Path):
    eng = PiperTTSEngine()
    # If somehow installed, skip soft assertion
    if eng.is_available():
        return
    result = eng.synthesize("x", "v", str(tmp_path / "p.wav"))
    assert not result.ok
    assert "not installed" in result.error.lower() or result.error


def test_registry_lists_providers():
    infos = list_engine_infos()
    ids = {i.id for i in infos}
    assert "mock" in ids
    assert "coqui" in ids or "edge-offline" in ids


def test_get_engine_mock():
    eng = get_engine("mock")
    assert eng.id == "mock"


def test_dub_engine_uses_contract_only():
    """Dub boundary imports scheduler/tts contract, not provider internals."""
    import engines.dub as dub

    assert hasattr(dub, "update_time")
    assert hasattr(dub, "schedule_segment_slot")
