"""TZ v4.0 P2 — SSML, LOCK gate, audio tempo ±5%, pre-lock polish."""

from __future__ import annotations


def test_ssml_break_capped_at_350():
    from engines.professional_dubbing.config import MAX_BREAK_MS
    from engines.professional_dubbing.prosody import build_prosody_plan

    assert MAX_BREAK_MS == 350
    plan = build_prosody_plan(
        "Перше речення. Друге речення. Третє.",
        segment_ms=8000,
        lang="uk",
        use_ssml=True,
    )
    import re

    breaks = [int(m) for m in re.findall(r'break time="(\d+)ms"', plan.text_for_tts)]
    assert breaks
    assert max(breaks) <= 350


def test_ssml_no_break_after_jr_abbrev():
    from engines.professional_dubbing.prosody import build_prosody_plan

    plan = build_prosody_plan(
        "George Jr. drove home for dinner.",
        segment_ms=5000,
        lang="en",
        use_ssml=True,
    )
    # Break should not appear immediately after Jr. period
    assert "Jr.<break" not in plan.text_for_tts
    assert "Jr</emphasis>.<break" not in plan.text_for_tts
    assert "drove home" in plan.text_for_tts


def test_ssml_no_break_after_false_name_period():
    from engines.professional_dubbing.prosody import build_prosody_plan

    plan = build_prosody_plan(
        "Джордж-молодший. зрозумів, що батько правий.",
        segment_ms=6000,
        lang="uk",
        use_ssml=True,
    )
    assert "молодший.<break" not in plan.text_for_tts


def test_pre_lock_polish_fiat_and_usc_and_name():
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    fiat = apply_pre_lock_polish(
        "хто буквально дав йому Фіат,.",
        original="who literally gave him the Fiat,",
    )
    assert ",." not in fiat
    assert "Фіат." in fiat or fiat.endswith("Фіат")

    usc = apply_pre_lock_polish(
        "програму з кінематографії з Ю Ес Сі, Південної Каліфорнії, Південної Каліфорнії, але",
        original="cinematography program at the University of Southern California",
    )
    assert usc.lower().count("південної каліфорнії") == 1

    name = apply_pre_lock_polish(
        "Джордж-молодший. зрозумів, що батько правий.",
        original="George Jr. had realized that dad had been right.",
    )
    assert "молодший. з" not in name.lower()


def test_lock_gate_defers_low_score():
    from engines.dsal.lock_gate import apply_lock_with_gate

    segments = [
        {
            "dsal_band": "red",
            "duration_match_score": 40,
            "clause_coverage": 0.5,
            "final_text": "короткий текст.",
            "text": "короткий текст.",
        },
        {
            "dsal_band": "green",
            "duration_match_score": 100,
            "clause_coverage": 1.0,
            "final_text": "нормальний повний рядок озвучки.",
            "text": "нормальний повний рядок озвучки.",
            "plain_text": "нормальний повний рядок озвучки.",
        },
    ]
    info = {"source_segments": ["short.", "full line of speech."], "segments_data": segments}

    def _lock(segs, info=None, advance_state=True):
        raise AssertionError("full lock should not run when gate fails")

    meta = apply_lock_with_gate(segments, info=info, lock_segments_fn=_lock)
    assert meta["translation_lock_deferred"] is True
    assert meta["needs_studio"] is True
    assert segments[0].get("needs_studio") is True
    assert segments[1].get("translation_locked") is True


def test_lock_gate_passes_green(monkeypatch):
    from engines.dsal.lock_gate import apply_lock_with_gate

    monkeypatch.setenv("VM_FORCE_TRANSLATION_LOCK", "")
    segments = [
        {
            "dsal_band": "green",
            "duration_match_score": 100,
            "clause_coverage": 1.0,
            "final_text": "повний природний рядок озвучки.",
            "plain_text": "повний природний рядок озвучки.",
        }
    ]
    info = {"source_segments": ["a full natural line."], "segments_data": segments}
    called = {"n": 0}

    def _lock(segs, info=None, advance_state=True):
        called["n"] += 1
        for s in segs:
            s["translation_locked"] = True
        if info is not None:
            info["translation_locked"] = True
        return {"locked_segments": 1, "pipeline_state": "LOCKED"}

    meta = apply_lock_with_gate(segments, info=info, lock_segments_fn=_lock)
    assert called["n"] == 1
    assert meta["pipeline_state"] == "LOCKED"
    assert not meta.get("translation_lock_deferred")


def test_audio_tempo_caps_pm5():
    from engines.audio_timing_optimizer import TEMPO_MAX, TEMPO_MIN
    from engines.conflict_resolver import SAFE_ATEMPO_MIN
    from engines.timing_fit import DUB_MAX_ATEMPO, _ATEMPO_MIN

    assert TEMPO_MIN == 0.95
    assert TEMPO_MAX == 1.05
    assert _ATEMPO_MIN == 0.95
    assert DUB_MAX_ATEMPO == 1.05
    assert SAFE_ATEMPO_MIN == 0.95
