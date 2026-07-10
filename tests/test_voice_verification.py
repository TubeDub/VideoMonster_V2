"""Tests for Voice Verification Agent — ASR compare and routing loop."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.voice_verification_agent.asr_compare import (
    VerificationMetrics,
    missing_words,
    route_verification_failure,
    text_similarity,
    truncated_words,
    verify_segment_audio,
)
from engines.ai_core.voice_verification_agent.verification_loop import (
    run_voice_verification_loop,
)


def test_text_similarity_identical():
    assert text_similarity("Привіт, світ!", "Привіт світ") >= 0.9


def test_missing_words_detects_gaps():
    missing = missing_words(
        "Привіт світ як справи",
        "Привіт світ",
    )
    assert "як" in missing or "справи" in missing


def test_truncated_words_detects_tail():
    truncated = truncated_words(
        "один два три чотири",
        "один два",
    )
    assert "три" in truncated or "чотири" in truncated


def test_route_low_similarity_to_voice():
    metrics = VerificationMetrics(
        similarity=0.55,
        issues=["low_similarity"],
        audio_completeness=0.95,
    )
    assert route_verification_failure(metrics, expected="abc", source="") == "voice"


def test_route_duration_overflow_to_timing():
    metrics = VerificationMetrics(
        similarity=0.90,
        issues=["duration_overflow"],
        audio_completeness=1.5,
    )
    assert route_verification_failure(metrics, expected="abc", source="") == "timing"


def test_route_language_mismatch_to_semantic():
    metrics = VerificationMetrics(
        similarity=0.40,
        issues=["language_mismatch"],
        language_match=False,
    )
    assert route_verification_failure(metrics, expected="Привіт", source="Hello") == "semantic"


def test_verify_segment_missing_wav_routes_voice(tmp_path):
    metrics = verify_segment_audio(
        expected_text="Привіт, світ, як справи?",
        wav_path=tmp_path / "missing.wav",
        target_lang="uk",
        slot_ms=2000,
    )
    assert metrics.passed is False
    assert "missing_wav" in metrics.issues
    assert metrics.route_to == "voice"


def test_verify_segment_with_mock_asr(tmp_path):
    wav = tmp_path / "seg.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)

    with patch(
        "engines.ai_core.voice_verification_agent.asr_compare.transcribe_wav_for_verification",
        return_value=("Привіт світ", 0.8, "uk"),
    ), patch(
        "engines.ai_core.voice_verification_agent.asr_compare.audio_duration_ms",
        return_value=1800,
    ):
        metrics = verify_segment_audio(
            expected_text="Привіт, світ, як у тебе справи сьогодні?",
            wav_path=wav,
            target_lang="uk",
            slot_ms=2000,
        )
    assert metrics.passed is False
    assert "missing_words" in metrics.issues or "low_similarity" in metrics.issues


def test_voice_verification_loop_routes_and_retries():
    manifest = {"source_lang": "en", "target_lang": "uk"}
    seg = {
        "index": 0,
        "text": "Hello world.",
        "grammar_text": "Привіт, світ, як у тебе справи сьогодні?",
        "tts_text": "Привіт, світ, як у тебе справи сьогодні?",
        "file": "seg0.wav",
        "slot_fit_score": 0.95,
        "start": 0.0,
        "end": 2.0,
    }
    state = {"segments": [seg]}

    call_count = {"n": 0}

    def fake_verify(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            m = VerificationMetrics(
                similarity=0.55,
                issues=["low_similarity"],
                route_to="voice",
                recognized_text="Привіт світ",
                audio_completeness=0.9,
            )
        else:
            m = VerificationMetrics(
                similarity=0.92,
                recognized_text="Привіт, світ, як у тебе справи сьогодні?",
                audio_completeness=0.95,
            )
        return m

    regen_calls: list[int] = []

    def fake_regen(idx, s, reason):
        regen_calls.append(idx)
        s = dict(s)
        s["file"] = "seg0_regen.wav"
        return s

    with patch(
        "engines.ai_core.voice_verification_agent.verification_loop.verify_segment_audio",
        side_effect=lambda **kw: fake_verify(**kw),
    ):
        segments, loop_log = run_voice_verification_loop(
            [seg],
            manifest=manifest,
            state=state,
            task_id="vv-test",
            target_lang="uk",
            regen_voice=fake_regen,
            max_cycles=2,
        )

    assert regen_calls == [0]
    assert segments[0].get("voice_verification_passed") is True
    assert loop_log[0]["pass"] is True


def test_voice_verification_agent_writes_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from engines.ai_core.voice_verification_agent import VoiceVerificationAgent

    manifest = {"project_uuid": "p-vv", "source_lang": "en", "target_lang": "uk"}
    seg = {
        "index": 0,
        "text": "Hello.",
        "grammar_text": "Привіт, світ, як у тебе справи сьогодні?",
        "tts_text": "Привіт, світ, як у тебе справи сьогодні?",
    }
    good = VerificationMetrics(
        similarity=0.95,
        recognized_text="Привіт, світ, як у тебе справи сьогодні?",
    )

    with patch(
        "engines.ai_core.voice_verification_agent.verification_loop.verify_segment_audio",
        return_value=good,
    ):
        agent = VoiceVerificationAgent(output_dir=tmp_path / "output")
        result = agent.run(manifest, {"segments": [seg]}, "vv-agent-test")

    assert result.status == "success"
    assert result.updated_state.get("voice_verification_passed") is True
    report_path = Path(result.updated_state["voice_verification_report_path"])
    assert report_path.is_file()
