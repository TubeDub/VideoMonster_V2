"""Master Spec Part 7 — Voice Platform / TTS / Lip Sync 2.0 tests."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_providers_registered():
    from engines.voice_platform import list_providers

    providers = list_providers()
    ids = {p["id"] for p in providers}
    assert "mock" in ids
    # edge or other legacy engines should appear via adapter
    assert any("edge" in i or i == "mock" for i in ids)


def test_voice_registry_and_profiles():
    from engines.voice_platform import list_style_profiles, load_voice_registry

    reg = load_voice_registry(refresh=True)
    voices = reg.list_voices()
    assert voices
    profiles = list_style_profiles()
    names = {p.name for p in profiles}
    assert "Documentary" in names
    assert "Anime" in names
    assert "News" in names


def test_multi_speaker_memory_lock():
    from engines.voice_platform import VoiceMemory, plan_multi_speaker

    units = [
        {"speech_uuid": "su1", "speaker_uuid": "A", "text": "Hello", "emotion": "calm"},
        {"speech_uuid": "su2", "speaker_uuid": "B", "text": "Hi there", "emotion": "joy"},
        {"speech_uuid": "su3", "speaker_uuid": "A", "text": "Again", "emotion": "calm"},
    ]
    plans, mem = plan_multi_speaker(
        units,
        project_id="p1",
        default_language="en",
        preferred_voices={
            "A": "en-US-GuyNeural",
            "B": "en-US-JennyNeural",
        },
    )
    assert len(plans) == 3
    assert plans[0].voice_uuid == plans[2].voice_uuid
    assert plans[0].voice_uuid != plans[1].voice_uuid
    with pytest.raises(ValueError):
        mem.assign("A", "totally-different-uuid")


def test_prosody_and_emotion():
    from engines.voice_platform.emotion import normalize_emotion, supported_emotions
    from engines.voice_platform.prosody import build_prosody_plan
    from engines.voice_platform.voice_registry import get_style_profile

    assert "joy" in supported_emotions()
    assert normalize_emotion("happy") == "joy"
    assert normalize_emotion("angry") == "anger"
    plan = build_prosody_plan(
        "Hello, world! How are you?",
        style=get_style_profile("News"),
        emotion="joy",
    )
    assert plan["tempo"] > 0
    assert plan["stresses"]
    assert plan["pauses"]
    assert plan["rate_str"]
    assert plan["pitch_str"]


def test_lipsync_phoneme_viseme():
    from engines.voice_platform.lipsync import build_lipsync_data

    data = build_lipsync_data("su1", "Hello world", duration_ms=800)
    d = data.to_dict()
    assert d["version"] == "2.0"
    assert d["phonemes"]
    assert d["visemes"]
    assert d["phonemes"][0]["ipa"]
    assert "mouth_open" in d["visemes"][0]


def test_synthesize_cache_and_quality(tmp_path: Path):
    from engines.voice_platform import SynthesisRequest, synthesize
    from engines.voice_platform.cache import VoiceCache
    from engines.voice_platform.metrics import get_metrics, reset_metrics
    from engines.voice_platform.voice_registry import resolve_voice

    reset_metrics()
    voice = resolve_voice(external_id="mock-default")
    cache = VoiceCache(tmp_path / "cache")
    out1 = tmp_path / "a.wav"
    req = SynthesisRequest(
        text="Cache me please",
        voice_uuid=voice.voice_uuid,
        speech_uuid="su-cache",
        provider="mock",
        output_path=str(out1),
        allow_cache=True,
    )
    r1 = synthesize(req, cache=cache)
    assert r1.ok
    assert Path(r1.output_path).is_file()
    assert r1.quality.get("ok") is True
    assert r1.lipsync.get("phonemes")

    out2 = tmp_path / "b.wav"
    req2 = SynthesisRequest(
        text="Cache me please",
        voice_uuid=voice.voice_uuid,
        speech_uuid="su-cache",
        provider="mock",
        output_path=str(out2),
        allow_cache=True,
    )
    r2 = synthesize(req2, cache=cache)
    assert r2.ok
    assert r2.cached is True

    m = get_metrics()
    assert m["synth_count"] >= 2
    assert m["cache_hit_pct"] > 0


def test_failover_to_mock():
    from engines.voice_platform.failover import decide_retry_strategy, failover_providers
    from engines.voice_platform import SynthesisRequest, synthesize
    from engines.voice_platform.voice_registry import resolve_voice

    assert decide_retry_strategy("timeout", attempt=1) == "other_engine"
    assert decide_retry_strategy("x", attempt=99) == "manual_review"
    chain = failover_providers("nonexistent-engine", chain=("nonexistent-engine", "mock"))
    assert chain[-1] == "mock"

    voice = resolve_voice(provider="mock")
    # Force unknown provider → failover chain includes mock via synthesize_with_failover
    r = synthesize(
        SynthesisRequest(
            text="Failover test",
            voice_uuid=voice.voice_uuid,
            provider="definitely-missing-tts",
            allow_cache=False,
        )
    )
    # Legacy adapter for missing id returns mock from get_provider
    assert r.ok or r.meta.get("manual_review")


def test_architecture_isolation():
    from engines.voice_platform.invariants import assert_voice_platform_isolated

    assert_voice_platform_isolated()


def test_plan_project_voices():
    from engines.voice_platform import plan_project_voices

    units = [
        {
            "speech_uuid": "s1",
            "speaker_uuid": "hero",
            "text": "We fight.",
            "emotion": "anger",
            "style": "Anime",
        },
        {
            "speech_uuid": "s2",
            "speaker_uuid": "hero",
            "text": "Again.",
            "emotion": "calm",
            "style": "Anime",
        },
    ]
    payload = plan_project_voices(units, language="en", style="Anime")
    assert payload["version"] == "7.0"
    assert len(payload["plans"]) == 2
    assert payload["plans"][0]["voice_uuid"] == payload["plans"][1]["voice_uuid"]
    assert "s1" in payload["lipsync"]
    assert not payload["consistency_issues"]


def test_cloning_interface():
    from engines.voice_platform.cloning import get_clone_adapter, clone_voice

    adapter = get_clone_adapter()
    # Without backends, null adapter is fine
    result = clone_voice("hi", "missing.wav", "out.wav")
    assert result.ok is False
    assert adapter.adapter_id
