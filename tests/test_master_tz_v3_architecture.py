"""MASTER TZ v3.0 architecture tests — P2/P6/P10/P11/P14/P20."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_architecture_violation_type_exists():
    from engines.pipeline_integrity import ArchitectureViolation, TranslationLockError

    err = ArchitectureViolation("x", rule="single_owner", segment_id="s1")
    assert err.code == "architecture_violation"
    assert err.rule == "single_owner"
    assert issubclass(TranslationLockError, Exception)


def test_fsm_includes_optimized_and_walk():
    from engines.pipeline_integrity.pipeline_state import (
        PipelineState,
        advance_pipeline_state,
        get_pipeline_state,
    )

    info: dict = {}
    # Spec Part 1 path (legacy aliases accepted)
    for st in (
        PipelineState.TRANSCRIBED,  # → RECOGNIZED
        PipelineState.TRANSLATED,
        PipelineState.VALIDATED,
        PipelineState.LOCKED,
        PipelineState.TTS_READY,  # → SPEECH_READY (walks PLANNED)
    ):
        advance_pipeline_state(info, st)
    assert get_pipeline_state(info) == PipelineState.SPEECH_READY
    # Legacy OPTIMIZED after speech is no-op (plan already passed)
    advance_pipeline_state(info, PipelineState.OPTIMIZED)
    assert get_pipeline_state(info) == PipelineState.SPEECH_READY
    advance_pipeline_state(info, PipelineState.SCHEDULED)
    assert get_pipeline_state(info) == PipelineState.SCHEDULED

    # Compat walk SPEECH_READY → SCHEDULED
    info2 = {"pipeline_state": "TTS_READY"}
    advance_pipeline_state(info2, PipelineState.SCHEDULED)
    assert get_pipeline_state(info2) == PipelineState.SCHEDULED


def test_overflow_manager_never_touches_text():
    from engines.pipeline_integrity.overflow_manager import register_overflow

    seg = {"segment_id": "a", "text": "KEEP", "translation_text": "KEEP"}
    rec = register_overflow(seg, index=0, overflow_ms=1332, slot_ms=5600)
    assert seg["text"] == "KEEP"
    assert seg["overflow"] is True
    assert rec.severity == "critical"
    assert "studio_manual_review" in rec.recovery_plan


def test_underflow_manager_forbids_text_expansion_plan():
    from engines.pipeline_integrity.underflow_manager import register_underflow

    seg = {"segment_id": "b", "translation_locked": True, "expand_required": True}
    rec = register_underflow(
        seg, index=1, shortfall_ms=800, slot_ms=5000, audio_ms=4200
    )
    assert "text_expansion" not in rec.recovery_plan
    assert seg.get("expand_required") is False


def test_post_tts_qa_respects_lock(monkeypatch):
    from engines import segment_timing_qa as qa

    seg = {
        "segment_id": "locked1",
        "translation_locked": True,
        "plain_text": "оригінал",
        "translation_text": "оригінал",
        "text": "оригінал",
        "file": "x.wav",
        "playback_duration": 7000,
        "tts_ms": 7000,
    }
    timing = [{"start": 0, "end": 5000}]

    monkeypatch.setattr(
        qa,
        "detect_post_tts_deviations",
        lambda *a, **k: [
            {
                "code": "duration_overflow",
                "slot_ms": 5000,
                "tts_ms": 7000,
            }
        ],
    )
    monkeypatch.setattr(qa, "segment_playback_ms", lambda s: 7000)
    monkeypatch.setattr(
        qa,
        "_update_speech_timing_diagnostics",
        lambda *a, **k: 7000,
    )

    called = {"rewrite": False}

    def _boom(*_a, **_k):
        called["rewrite"] = True
        raise AssertionError("must not rewrite after LOCK")

    monkeypatch.setattr(
        "engines.semantic_optimizer.optimize_llm_rephrase_for_slot",
        _boom,
    )
    monkeypatch.setattr(
        "engines.semantic_optimizer.optimize_expand_for_slot",
        _boom,
    )

    stats = qa.post_tts_validate_and_retry(
        [seg],
        timing,
        source_segments=["src"],
        voice="uk-UA-OstapNeural",
        target_lang="uk",
        src_lang="en",
        max_retries=2,
    )
    assert called["rewrite"] is False
    assert seg["plain_text"] == "оригінал"
    assert seg.get("overflow") is True
    assert seg.get("overflow_manager")


def test_audio_identity_hard_fail(tmp_path):
    from engines.pipeline_integrity.audio_identity import ensure_unique_before_handoff
    from engines.pipeline_integrity.exceptions import PipelineAudioIdentityError

    f = tmp_path / "shared.wav"
    f.write_bytes(b"RIFF")
    segs = [
        {"segment_id": "s1", "file": str(f)},
        {"segment_id": "s2", "file": str(f)},
    ]
    with pytest.raises(PipelineAudioIdentityError):
        ensure_unique_before_handoff(
            segs,
            resolve_path=lambda p: Path(p),
            dest_dir=tmp_path,
            run_id="t1",
            hard_fail=True,
        )


def test_translation_freeze_doc_exists():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "TRANSLATION_ENGINE_FREEZE_P1.md").is_file()
    assert (root / "docs" / "ARCHITECTURE_REPORT_MASTER_TZ_V3_P0.md").is_file()
