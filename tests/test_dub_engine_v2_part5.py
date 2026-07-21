"""Master Spec Part 5 — Dub Engine 2.0 tests."""

from __future__ import annotations

import pytest


def _locked_sentences():
    from engines.semantic_v3.types import SemanticSentence

    a = SemanticSentence(
        text="Hello George.",
        translated_text="Привіт George.",
        start_ms=0,
        end_ms=2000,
        predicted_tts_ms=1800,
        entities=["George"],
        semantic_locked=True,
        lock_status="locked",
        style="Movie",
        emotion="calm",
        speaker="A",
        scene_uuid="sc1",
    )
    b = SemanticSentence(
        text="How are you?",
        translated_text="Як справи?",
        start_ms=2200,
        end_ms=4000,
        predicted_tts_ms=1500,
        semantic_locked=True,
        lock_status="locked",
        style="Movie",
        emotion="calm",
        speaker="B",
        scene_uuid="sc1",
        is_dialogue=True,
    )
    a.recovery_plan = ["trim_silence", "tempo", "ready"]
    b.recovery_plan = ["pause_optimization", "ready"]
    return [a, b]


def test_ato_order_fixed():
    from engines.dub_engine_v2 import ATO_ORDER
    from engines.dub_engine_v2.timing import assert_ato_order, normalize_steps
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    assert ATO_ORDER[0] == "trim_silence"
    assert ATO_ORDER[-1] == "manual_review"
    assert normalize_steps(["tempo", "trim_silence"]) == ["trim_silence", "tempo"]
    with pytest.raises(ArchitectureViolation):
        assert_ato_order(["tempo", "trim_silence"])


def test_dub_engine_plans_before_tts_no_text_mutation():
    from engines.dub_engine_v2 import run_dub_engine

    sentences = _locked_sentences()
    before = [s.translated_text for s in sentences]
    result = run_dub_engine(sentences, voice="default", profile="Movie")
    assert result.speech_units
    assert result.audio_plans
    assert len(result.timeline.units) == len(result.speech_units)
    assert result.lipsync
    assert result.metrics.speech_flow_score > 0
    assert [s.translated_text for s in sentences] == before
    # Phoneme predictor used (not char count) — durations positive
    assert all(p.duration_ms > 0 for p in result.audio_plans)


def test_overlap_hard_fail():
    from engines.dub_engine_v2.detectors import detect_overlaps
    from engines.dub_engine_v2.models import AudioUnitV2, ProjectTimeline
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    tl = ProjectTimeline(
        units=[
            AudioUnitV2("a1", "s1", start_ms=0, end_ms=1000, duration=1000),
            AudioUnitV2("a2", "s2", start_ms=900, end_ms=1900, duration=1000),
        ]
    )
    with pytest.raises(ArchitectureViolation):
        detect_overlaps(tl, hard_fail=True)


def test_tail_spill_one_to_one():
    from engines.dub_engine_v2.detectors import detect_tail_spill
    from engines.dub_engine_v2.models import AudioUnitV2, ProjectTimeline, SpeechUnitV2
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    su = SpeechUnitV2(
        speech_uuid="s1",
        sentence_uuid="x",
        speaker_uuid="A",
        scene_uuid="sc",
        text="Hi",
        source_text="Hi",
        start_ms=0,
        end_ms=1000,
    )
    tl = ProjectTimeline(
        units=[
            AudioUnitV2("a1", "s1", start_ms=0, end_ms=500, duration=500),
            AudioUnitV2("a2", "s1", start_ms=500, end_ms=1000, duration=500),
        ]
    )
    with pytest.raises(ArchitectureViolation):
        detect_tail_spill([su], tl, hard_fail=True)


def test_scheduler_is_time_owner_api():
    from engines.dub_engine_v2.models import AudioUnitV2, ProjectTimeline
    from engines.dub_engine_v2.scheduler import update_audio_time

    tl = ProjectTimeline(
        units=[AudioUnitV2("a1", "s1", start_ms=0, end_ms=1000, duration=1000)]
    )
    tl2 = update_audio_time(tl, "a1", end_ms=1200)
    assert tl2.units[0].end_ms == 1200
    assert tl2.units[0].version == 2


def test_audio_quality_blocks_bad_duration():
    from engines.dub_engine_v2.models import AudioUnitV2
    from engines.dub_engine_v2.quality import validate_audio_units
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    with pytest.raises(ArchitectureViolation):
        validate_audio_units(
            [AudioUnitV2("a1", "s1", start_ms=0, end_ms=0, duration=0)]
        )


def test_isolation_from_translation_adapt():
    from engines.dub_engine_v2.invariants import assert_dub_engine_isolated

    assert_dub_engine_isolated()


def test_phase2_includes_dub_engine_v2(monkeypatch):
    monkeypatch.setenv("VM_TRANSLATION_BACKEND", "heuristic")
    from engines.semantic_v3.phase2 import run_semantic_v3_phase2

    proj = run_semantic_v3_phase2(
        ["Hello George. How are you?"],
        [{"start": 0, "end": 4000}],
        translate=True,
        content_mode="Movie",
    )
    assert proj.meta.get("dub_engine_v2") is True
    assert proj.meta.get("audio_metrics")
    assert proj.meta.get("timeline", {}).get("units")
