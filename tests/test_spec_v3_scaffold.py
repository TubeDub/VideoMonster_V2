"""Spec v3 scaffold — smoke tests for every new module.

Tests only pure Python logic; heavy models (pyannote / speechbrain / htdemucs)
are optional and gracefully skipped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─── P0-D: STT quality policy ────────────────────────────────────────────────


def test_stt_quality_default_is_simple(monkeypatch):
    monkeypatch.delenv("VM_STT_QUALITY", raising=False)
    from engines.simple_stt_policy import resolve_stt_quality

    assert resolve_stt_quality({}) == "simple"


def test_stt_quality_high_via_task_info():
    from engines.simple_stt_policy import (
        resolve_stt_model_for_quality,
        resolve_stt_quality,
        word_timestamps_for_quality,
    )

    q = resolve_stt_quality({"stt_quality": "high"})
    assert q == "high"
    assert resolve_stt_model_for_quality("high") == "large-v3"
    assert word_timestamps_for_quality("high") is True


def test_stt_quality_high_via_env(monkeypatch):
    monkeypatch.setenv("VM_STT_QUALITY", "high")
    from engines.simple_stt_policy import resolve_stt_quality

    assert resolve_stt_quality({}) == "high"


def test_stt_quality_spec_v3_flag():
    from engines.simple_stt_policy import resolve_stt_quality

    assert resolve_stt_quality({"spec_v3": True}) == "high"


def test_simple_stt_policy_high_quality_stamps_word_ts_and_no_lock():
    from engines.simple_stt_policy import apply_simple_stt_policy

    info: dict = {"stt_quality": "high"}
    apply_simple_stt_policy(info)
    assert info["stt_model"] == "large-v3"
    assert info["stt_word_timestamps"] is True
    assert info["simple_stt_locked"] is False


# ─── P0-B: 4-stem separation flag ───────────────────────────────────────────


def test_four_stem_enabled_via_task_info():
    from engines.source_separation import is_four_stem_enabled

    assert is_four_stem_enabled({"spec_v3": True}) is True
    assert is_four_stem_enabled({"stems_v3": True}) is True
    assert is_four_stem_enabled({}) is False


def test_four_stem_enabled_via_env(monkeypatch):
    monkeypatch.setenv("VM_4STEM", "1")
    from engines.source_separation import is_four_stem_enabled

    assert is_four_stem_enabled({}) is True


def test_separation_result_stems_v3_dataclass():
    from engines.source_separation import SeparationResult

    r = SeparationResult(stems_v3={"vocals": "/tmp/v.wav"}, stems_count=4)
    d = r.to_dict()
    assert d["stems_v3"] == {"vocals": "/tmp/v.wav"}
    assert d["stems_count"] == 4


# ─── P0-A: Diarization scaffold ─────────────────────────────────────────────


def test_diarization_disabled_by_default():
    from engines.diarization import is_diarization_enabled, run_diarization

    assert is_diarization_enabled({}) is False
    res = run_diarization("/nonexistent.wav", task_info={})
    assert res.enabled is False
    assert res.attempted is False


def test_diarization_single_speaker_fallback(tmp_path):
    from pydub import AudioSegment

    from engines.diarization import run_diarization

    wav = tmp_path / "clip.wav"
    AudioSegment.silent(duration=2000).export(str(wav), format="wav")
    res = run_diarization(str(wav), task_info={"spec_v3": True})
    # Without pyannote installed this must degrade — never raise.
    assert res.attempted is True
    assert res.success is True
    assert "SPEAKER_00" in res.speakers


def test_assign_speakers_to_segments_by_overlap():
    from engines.diarization import (
        DiarizationResult,
        SpeakerTurn,
        assign_speakers_to_segments,
    )

    dia = DiarizationResult(
        enabled=True,
        attempted=True,
        success=True,
        method="test",
        turns=[
            SpeakerTurn(start_ms=0, end_ms=5000, speaker="SPK_A"),
            SpeakerTurn(start_ms=5000, end_ms=10000, speaker="SPK_B"),
        ],
        speakers=["SPK_A", "SPK_B"],
    )
    segs = [
        {"index": 0, "start_ms": 100, "end_ms": 4500},
        {"index": 1, "start_ms": 5500, "end_ms": 9500},
        {"index": 2, "start_ms": 4900, "end_ms": 5200},  # tie-ish → still picks one
    ]
    assign_speakers_to_segments(segs, dia)
    assert segs[0]["speaker"] == "SPK_A"
    assert segs[1]["speaker"] == "SPK_B"
    assert segs[2]["speaker"] in {"SPK_A", "SPK_B"}
    assert segs[0]["speaker_confidence"] > 0.9


# ─── P0-C: Speaker verification ─────────────────────────────────────────────


def test_speaker_verify_missing_files_returns_zero():
    from engines.speaker_verification import verify

    v = verify("/no/ref.wav", "/no/cand.wav")
    assert v["ok"] is False
    assert v["similarity"] == 0.0
    assert "method" in v


def test_speaker_verify_retry_stops_on_none_synth():
    from engines.speaker_verification import retry_until_verified

    v = retry_until_verified(
        lambda i: None, "/no/ref.wav", threshold=0.99, max_attempts=3
    )
    assert v["ok"] is False
    assert v.get("error") == "reference_embed_failed"


@pytest.mark.skipif(
    True,  # heavy — only run if librosa+soundfile installed AND user opts in
    reason="Skipped by default: requires audio embed backend",
)
def test_speaker_verify_identity_high_similarity(tmp_path):
    from pydub import AudioSegment
    from pydub.generators import Sine

    from engines.speaker_verification import verify

    tone = Sine(220).to_audio_segment(duration=3000)
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    tone.export(str(a), format="wav")
    tone.export(str(b), format="wav")
    v = verify(str(a), str(b), threshold=0.5)
    assert v["similarity"] > 0.5


# ─── P1: Spec v3 errors + Semantic Gate ─────────────────────────────────────


def test_spec_v3_error_hierarchy():
    from engines.spec_v3_errors import (
        LanguageLeakError,
        SemanticIntegrityError,
        SpecV3Error,
        TimingBudgetError,
        VoiceIdentityError,
    )

    for cls in (
        LanguageLeakError,
        SemanticIntegrityError,
        TimingBudgetError,
        VoiceIdentityError,
    ):
        err = cls("boom", segment_index=1)
        assert isinstance(err, SpecV3Error)
        d = err.to_dict()
        assert d["code"] == cls.code
        assert d["context"]["segment_index"] == 1


def test_semantic_gate_ok_when_translation_in_uk():
    from engines.spec_v3_semantic_gate import check_translation

    diag = check_translation(
        "The dog is running fast.",
        "Собака швидко біжить.",
        target_lang="uk",
        source_lang="en",
        strict=False,
    )
    assert diag["language_leak"] is False
    assert diag["final_status"] in {"ok", "semantic_degraded"}  # score may vary


def test_semantic_gate_language_leak_raises_strict():
    from engines.spec_v3_errors import LanguageLeakError
    from engines.spec_v3_semantic_gate import check_translation

    with pytest.raises(LanguageLeakError):
        check_translation(
            "Собака швидко біжить.",
            "The dog is running very fast in English only.",
            target_lang="uk",
            source_lang="uk",
            strict=True,
        )


def test_semantic_gate_batch_stamps_segments():
    from engines.spec_v3_semantic_gate import check_segments_batch

    segs = [
        {
            "index": 0,
            "text": "Собака швидко біжить.",
            "translated_text": "The dog is running fast.",
        },
        {
            "index": 1,
            "text": "Кіт спить на дивані.",
            "translated_text": "Кіт спить на дивані.",
        },
    ]
    summary = check_segments_batch(
        segs, target_lang="uk", source_lang="uk", strict=False
    )
    assert summary["total"] == 2
    # seg 0 should be flagged as leak (EN in UK track)
    assert 0 in summary["language_leak_indices"]
    for seg in segs:
        assert "spec_v3_language_gate" in seg


# ─── P1: Cleanup keeps spec v3 dirs and voice profile files ─────────────────


def test_cleanup_protects_speaker_reference_files(tmp_path):
    from pydub import AudioSegment

    from engines.pipeline_cleanup import cleanup_intermediate_work_dirs

    session = tmp_path
    slot_fit = session / "slot_fit"
    slot_fit.mkdir()
    # protected by prefix
    (slot_fit / "speaker_SPK_A.wav").write_bytes(b"RIFFxxxxWAVEfmt ")
    (slot_fit / "tts_regen_0.wav").write_bytes(b"RIFFxxxxWAVEfmt ")
    # unprotected
    (slot_fit / "junk.txt").write_text("noise")

    removed = cleanup_intermediate_work_dirs(session, keep_segment_audio=True)
    # salvaged to session root
    assert (session / "speaker_SPK_A.wav").is_file()
    assert (session / "tts_regen_0.wav").is_file()
    assert removed >= 1


# ─── P2: Stage restart ──────────────────────────────────────────────────────


def test_stage_restart_save_and_resume(tmp_path):
    from engines.stage_restart import (
        STAGES,
        is_stage_complete,
        last_completed_stage,
        list_stages,
        load_stage,
        resume_from,
        save_stage,
    )

    session = tmp_path / "sess"
    session.mkdir()
    save_stage(session, "stt", {"segments": [{"id": 0}]}, run_id="run1")
    save_stage(session, "diarization", {"speakers": ["SPK_00"]}, run_id="run1")

    assert last_completed_stage(session) == "diarization"
    # Next after diarization is `translate` per STAGES tuple
    assert resume_from(session) == "translate"
    assert is_stage_complete(session, "stt")
    assert not is_stage_complete(session, "translate")
    assert load_stage(session, "stt") == {"segments": [{"id": 0}]}
    assert len(list_stages(session)) == 2


def test_stage_restart_reset_from_drops_subsequent(tmp_path):
    from engines.stage_restart import (
        is_stage_complete,
        reset_from,
        save_stage,
    )

    session = tmp_path
    save_stage(session, "stt", {"a": 1})
    save_stage(session, "diarization", {"b": 2})
    save_stage(session, "translate", {"c": 3})

    dropped = reset_from(session, "diarization")
    assert dropped >= 1
    assert is_stage_complete(session, "stt")
    assert not is_stage_complete(session, "diarization")
    assert not is_stage_complete(session, "translate")


def test_stage_restart_idempotent_replace(tmp_path):
    from engines.stage_restart import list_stages, save_stage

    save_stage(tmp_path, "stt", {"a": 1})
    save_stage(tmp_path, "stt", {"a": 2})  # rerun same stage
    entries = list_stages(tmp_path)
    stt_entries = [e for e in entries if e["stage"] == "stt"]
    assert len(stt_entries) == 1


def test_stage_restart_manifest_is_valid_json(tmp_path):
    from engines.stage_restart import DIR_NAME, MANIFEST_NAME, save_stage

    save_stage(tmp_path, "stt", {"x": 1})
    manifest = tmp_path / DIR_NAME / MANIFEST_NAME
    assert manifest.is_file()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data[0]["stage"] == "stt"
