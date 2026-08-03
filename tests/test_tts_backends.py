# -*- coding: utf-8 -*-
"""Stage 20 — Ukrainian TTS backends factory / aliases / fallback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_normalize_backend_aliases():
    from engines.tts_backends import (
        ENGINE_EDGE,
        ENGINE_PIPER,
        ENGINE_TTS_UK,
        normalize_backend_name,
    )

    assert normalize_backend_name("edge") == ENGINE_EDGE
    assert normalize_backend_name("edge-offline") == ENGINE_EDGE
    assert normalize_backend_name("tts_uk") == ENGINE_TTS_UK
    assert normalize_backend_name("tts-uk") == ENGINE_TTS_UK
    assert normalize_backend_name("piper") == ENGINE_PIPER


def test_resolve_voice_for_backend_defaults_and_maps():
    from engines.tts_backends import resolve_voice_for_backend

    assert resolve_voice_for_backend("", "tts_uk") == "mykyta"
    assert resolve_voice_for_backend("", "piper") == "uk_UA-mykyta-high"
    assert resolve_voice_for_backend("", "edge") == "uk-UA-OstapNeural"
    assert resolve_voice_for_backend("uk-UA-OstapNeural", "tts_uk") == "mykyta"
    assert resolve_voice_for_backend("uk-UA-PolinaNeural", "piper") == "uk_UA-tetiana-high"
    assert resolve_voice_for_backend("tts_uk:mykyta", "edge") == "uk-UA-OstapNeural"
    assert resolve_voice_for_backend("mykyta", "edge-offline") == "uk-UA-OstapNeural"
    assert resolve_voice_for_backend("uk_UA-tetiana-high", "edge") == "uk-UA-PolinaNeural"


def test_is_uk_tts_voice_accepts_all_backends():
    from engines.tts_backends import is_uk_tts_voice

    assert is_uk_tts_voice("uk-UA-OstapNeural")
    assert is_uk_tts_voice("mykyta")
    assert is_uk_tts_voice("uk_UA-mykyta-high")
    assert is_uk_tts_voice("piper:uk_UA-lada-high")
    assert not is_uk_tts_voice("ru-RU-DmitryNeural")


def test_voices_for_backend_lists():
    from engines.tts_backends import voices_for_backend

    edge = {v["id"] for v in voices_for_backend("edge")}
    uk = {v["id"] for v in voices_for_backend("tts_uk")}
    piper = {v["id"] for v in voices_for_backend("piper")}
    assert "uk-UA-OstapNeural" in edge
    assert "mykyta" in uk
    assert "uk_UA-mykyta-high" in piper


def test_stamp_tts_backend_meta():
    from engines.tts_backends import stamp_tts_backend_meta

    seg: dict = {}
    stamp_tts_backend_meta(seg, engine_id="tts_uk", voice="mykyta")
    assert seg["tts_backend"] == "tts_uk"
    assert seg["tts_voice"] == "mykyta"
    assert seg["tts_sample_rate"] == 44100

    seg2: dict = {}
    stamp_tts_backend_meta(seg2, engine_id="piper", voice="uk_UA-oleksa-high")
    assert seg2["tts_backend"] == "piper"
    assert seg2["tts_voice"] == "uk_UA-oleksa-high"


def test_rate_to_length_scale():
    from engines.tts_backends import rate_to_length_scale

    assert rate_to_length_scale("+10%") == pytest.approx(0.9)
    assert rate_to_length_scale("-10%") == pytest.approx(1.1)
    assert rate_to_length_scale("") == 1.0


def test_get_tts_backend_falls_back_when_unavailable():
    from engines.tts_backends import ENGINE_EDGE, get_tts_backend

    fake_uk = MagicMock()
    fake_uk.id = "tts_uk"
    fake_uk.is_available.return_value = False
    fake_edge = MagicMock()
    fake_edge.id = ENGINE_EDGE
    fake_edge.is_available.return_value = True

    def _get(eid):
        if eid == "tts_uk":
            return fake_uk
        return fake_edge

    with patch("engines.tts_engines.registry.get_engine", side_effect=_get):
        eng = get_tts_backend("tts_uk")
    assert eng.id == ENGINE_EDGE


def test_synthesize_with_backend_fallback_on_failure(tmp_path: Path):
    from engines.tts_backends import synthesize_with_backend
    from engines.tts_engines.base import TTSResult

    out = str(tmp_path / "out.mp3")
    calls = []

    def _synth(text, voice, output_path, *, engine_id=None, rate=None, pitch=None, **_kw):
        calls.append(engine_id)
        if engine_id == "tts_uk":
            return TTSResult(ok=False, engine_id="tts_uk", error="missing")
        return TTSResult(ok=True, engine_id="edge-offline", output_path=output_path)

    with patch("engines.tts_engines.registry.synthesize", side_effect=_synth):
        result = synthesize_with_backend(
            "Привіт", "mykyta", out, engine_id="tts_uk"
        )
    assert result.ok
    assert calls == ["tts_uk", "edge-offline"]


def test_pipeline_context_backend_affects_normalize():
    from engines.tts_backends import (
        ENGINE_TTS_UK,
        normalize_backend_name,
        set_pipeline_tts_backend,
    )

    set_pipeline_tts_backend("tts_uk")
    try:
        assert normalize_backend_name(None) == ENGINE_TTS_UK
    finally:
        set_pipeline_tts_backend(None)


def test_assert_voice_matches_target_accepts_tts_uk():
    from engines.tts_lang_lock import assert_voice_matches_target

    ok, _ = assert_voice_matches_target("mykyta", "uk", raise_error=False)
    assert ok
    ok2, _ = assert_voice_matches_target("uk_UA-mykyta-high", "uk", raise_error=False)
    assert ok2
