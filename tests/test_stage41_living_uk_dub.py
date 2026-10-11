# -*- coding: utf-8 -*-
"""Stage 41 — living UK dub: roles, tighter Simple tempo, anti-repeat (offline)."""

from __future__ import annotations

import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _wav(path: Path, ms: int = 500, sr: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(ms / 1000.0 * sr))
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(b"\x00\x00" * frames)
    return path


def _dialogue_segs():
    return [
        {
            "index": 0,
            "text": "Привіт, Джонатане.",
            "start_ms": 0,
            "end_ms": 1800,
            "slot_ms": 1800,
        },
        {
            "index": 1,
            "text": "Так, я тут.",
            "start_ms": 2300,
            "end_ms": 4000,
            "slot_ms": 1700,
        },
        {
            "index": 2,
            "text": "Як машина?",
            "start_ms": 4500,
            "end_ms": 6000,
            "slot_ms": 1500,
        },
        {
            "index": 3,
            "text": "Добре їде.",
            "start_ms": 6500,
            "end_ms": 8200,
            "slot_ms": 1700,
        },
    ]


def test_two_speaker_heuristic_assigns_uk_roles():
    from engines.diarization import SPEAKER_A, SPEAKER_B, ensure_speaker_ids
    from engines.simple_voice_lock import lock_simple_pipeline_voice

    segs = _dialogue_segs()
    smap = ensure_speaker_ids(segs)
    roles = set(smap.get("speaker_ids") or [])
    assert SPEAKER_A in roles and SPEAKER_B in roles
    assert segs[0]["speaker_id"] == SPEAKER_A
    assert segs[1]["speaker_id"] == SPEAKER_B
    assert segs[0]["speaker_id"] != segs[1]["speaker_id"]

    info = {"target_lang": "uk", "simple_pipeline": True, "happy_path": True}
    stamp = lock_simple_pipeline_voice(
        segs, pipeline_voice="uk-UA-OstapNeural", task_info=info
    )
    voices = {str(s.get("voice") or "") for s in segs}
    assert len(voices) >= 2, voices
    assert stamp["unique_voices_used"] >= 2
    for v in voices:
        low = v.lower()
        assert not low.startswith(("cs-cz", "sk-sk", "pl-pl", "hu-hu"))
        assert (
            v.startswith("uk-UA-")
            or low in ("mykyta", "tetiana", "lada")
            or v.startswith("uk_UA-")
        )


def test_per_speaker_voice_lock_does_not_wipe_b():
    from engines.oss_production import lock_voice_after_first_success

    items = [
        {
            "index": 0,
            "speaker_id": "SPEAKER_A",
            "voice": "mykyta",
            "engine_id": "tts_uk",
        },
        {
            "index": 1,
            "speaker_id": "SPEAKER_B",
            "voice": "uk-UA-OstapNeural",
            "engine_id": "edge-offline",
        },
        {
            "index": 2,
            "speaker_id": "SPEAKER_A",
            "voice": "uk-UA-PolinaNeural",
            "engine_id": "edge-offline",
        },
    ]
    stamp = lock_voice_after_first_success(
        items,
        voice="mykyta",
        engine_id="tts_uk",
        speaker_id="SPEAKER_A",
    )
    assert stamp.get("per_speaker_voice_lock") is True
    assert items[0]["voice"] == "mykyta"
    assert items[2]["voice"] == "mykyta"
    assert items[1]["voice"] == "uk-UA-OstapNeural"
    assert items[1]["engine_id"] == "edge-offline"


def test_uk_simple_atempo_clamp_and_no_atempo_before_text_fit():
    from engines.text_slot_fit import (
        STAGE31_ATEMPO_MAX,
        STAGE41_UK_SIMPLE_ATEMPO_MAX,
        STAGE41_UK_SIMPLE_ATEMPO_MIN,
        STAGE41_UK_SIMPLE_LENGTH_SCALE_MAX,
        allow_atempo_after_text_fit,
        clamp_stage31_tempo,
        clamp_uk_simple_atempo,
        clamp_uk_simple_length_scale,
        stage31_duration_levers,
    )

    assert STAGE31_ATEMPO_MAX == 1.08
    assert clamp_stage31_tempo(1.30) == 1.08
    assert clamp_uk_simple_atempo(1.30) == STAGE41_UK_SIMPLE_ATEMPO_MAX
    assert clamp_uk_simple_atempo(0.70) == STAGE41_UK_SIMPLE_ATEMPO_MIN
    assert STAGE41_UK_SIMPLE_ATEMPO_MIN >= 0.97
    assert 0.94 <= clamp_uk_simple_atempo(0.90) <= 1.06
    assert clamp_stage31_tempo(1.30, uk_simple=True) == 1.06
    assert clamp_uk_simple_length_scale(1.10) <= 1.02
    assert STAGE41_UK_SIMPLE_LENGTH_SCALE_MAX <= 1.02

    levers = stage31_duration_levers(slot_ms=3000, tts_ms=5000)
    assert levers[0] == "text_slot_fit"
    assert levers.index("text_slot_fit") < levers.index("atempo")

    bare = {"target_lang": "uk", "simple_pipeline": True, "uk_simple_prosody": True}
    assert allow_atempo_after_text_fit(bare) is False
    bare["duration_control_used"] = "text_slot_fit"
    assert allow_atempo_after_text_fit(bare) is True


def test_anti_repeat_eighty_percent_overlap_rewrites():
    from engines.repetition_guard import (
        block_or_rewrite_repeat,
        is_near_repeat,
        token_overlap_ratio,
    )

    prev = "Так, це нормально тут у машині сьогодні."
    cur = "Так, це нормально тут у машині сьогодні так."
    assert token_overlap_ratio(cur, prev) >= 0.80
    assert is_near_repeat(cur, prev)
    raw = "Добре, я зрозумів що відбувається зараз."
    out, changed = block_or_rewrite_repeat(cur, prev, raw_mt=raw)
    assert changed is True
    assert out != cur
    assert not is_near_repeat(out, prev)


def test_three_clause_loop_stripped():
    from engines.repetition_guard import strip_looped_clauses

    text = "він поїхав додому. він поїхав додому. він поїхав додому."
    out, changed = strip_looped_clauses(text)
    assert changed is True
    assert out.lower().count("поїхав додому") == 1


def test_cross_speaker_merge_refused():
    from engines.translation_naturalizer import (
        merge_segments_for_tts,
        refuse_cross_speaker_merge,
    )

    assert refuse_cross_speaker_merge("SPEAKER_A", "SPEAKER_B") is True
    assert refuse_cross_speaker_merge("SPEAKER_A", "SPEAKER_A") is False
    texts = ["Привіт.", "Так.", "Добре."]
    timing = [{"start": 0, "end": 800}, {"start": 900, "end": 1700}, {"start": 1800, "end": 2600}]
    groups = merge_segments_for_tts(
        texts,
        timing,
        min_duration_ms=4500,
        speaker_ids=["SPEAKER_A", "SPEAKER_B", "SPEAKER_A"],
    )
    assert all(len(g) == 1 for g in groups), groups


def test_diagnostics_speakers_prosody_text_quality(tmp_path):
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    wav = _wav(tmp_path / "pad_silence_x.wav", ms=800)
    seg = {
        "index": 0,
        "text": "Привіт",
        "final_tts_text": "Привіт",
        "speaker_id": "SPEAKER_A",
        "tts_voice": "mykyta",
        "tts_backend": "tts_uk",
        "tts_language": "uk",
        "atempo": 1.02,
        "tts_length_scale": 1.04,
        "resolved_path": str(wav),
        "file": wav.name,
        "audio_padded": True,
    }
    info = {
        "task_id": "s41diag",
        "session_dir": str(tmp_path),
        "target_lang": "uk",
        "simple_pipeline": True,
        "repeat_blocked_count": 2,
        "overlap_count": 0,
    }
    block = _build_openddf_tts_pipeline_block(info, segments_data=[seg])
    assert "speakers" in block
    assert block["speakers"][0]["id"] == "SPEAKER_A"
    assert block["speakers"][0]["voice"]
    assert "segment_indices" in block["speakers"][0]
    assert "prosody" in block
    assert "atempo_min" in block["prosody"]
    assert "atempo_max_used" in block["prosody"]
    assert "length_scale_used" in block["prosody"]
    assert "text_quality" in block
    assert "repeat_blocked_count" in block["text_quality"]
    assert "unresolved_count" in block["text_quality"]
    assert "llm_used" in block["text_quality"]
    assert block["audio_missing"] == 0


def test_strip_slot_pad_fillers_invoked_on_simple_path():
    from engines.simple_mt_path import finalize_living_uk_segments
    from engines.text_slot_fit import strip_slot_pad_fillers

    dirty = "О, напевно, вже два дні минуло, саме тоді, і саме в цей момент."
    assert "саме тоді" not in strip_slot_pad_fillers(dirty).lower()
    out, meta = finalize_living_uk_segments(
        ["Oh it's probably been two days."],
        [dirty],
    )
    assert out
    low = out[0].lower()
    assert "саме тоді" not in low
    assert "і саме в цей момент" not in low
    assert "ось як це було тоді" not in low
    assert meta.get("pads_stripped", 0) >= 0


def test_mykyta_preferred_over_ostap_when_tts_uk_available(monkeypatch):
    from engines.simple_voice_lock import resolve_uk_role_voices
    from engines import tts_lang_lock

    monkeypatch.setattr(tts_lang_lock, "_tts_uk_backend_available", lambda: True)
    monkeypatch.setattr(
        "engines.simple_voice_lock._tts_uk_available", lambda: True
    )
    pool = resolve_uk_role_voices(
        {"target_lang": "uk", "simple_pipeline": True},
        tts_uk_available=True,
    )
    assert pool["SPEAKER_A"]["voice"] == "mykyta"
    assert pool["SPEAKER_A"]["backend"] == "tts_uk"
    assert pool["SPEAKER_B"]["voice"] != "mykyta"
    assert pool["SPEAKER_B"]["voice"] != pool["SPEAKER_A"]["voice"]
    b = str(pool["SPEAKER_B"]["voice"]).lower()
    assert not b.startswith(("cs-cz", "sk-sk"))


def test_uk_simple_defaults_not_slower_than_realtime(monkeypatch):
    from engines.simple_dub_pipeline import apply_simple_pipeline_policy
    from engines.simple_voice_lock import (
        EDGE_RATE_SPEAKER_A,
        EDGE_RATE_SPEAKER_B,
        living_uk_edge_pitch,
        living_uk_edge_rate,
        lock_simple_pipeline_voice,
    )
    from engines.tts_backends import (
        MYKYTA_LENGTH_SCALE_DEFAULT,
        MYKYTA_RATE_DEFAULT,
    )
    from engines import tts_lang_lock

    monkeypatch.setattr(tts_lang_lock, "_tts_uk_backend_available", lambda: True)
    monkeypatch.setattr("engines.simple_voice_lock._tts_uk_available", lambda: True)

    assert MYKYTA_RATE_DEFAULT >= 1.00
    assert MYKYTA_LENGTH_SCALE_DEFAULT <= 1.00
    info = {"target_lang": "uk", "user_mode": "basic", "tts_engine": "edge-offline"}
    apply_simple_pipeline_policy(info, user_mode="basic")
    assert float(info.get("mykyta_rate") or 0) >= 1.00
    assert float(info.get("mykyta_length_scale") or 1) <= 1.00
    assert info.get("tts_engine") == "tts_uk"
    assert float(info.get("min_atempo") or 0) >= 0.97

    assert living_uk_edge_rate("SPEAKER_A") == EDGE_RATE_SPEAKER_A
    assert living_uk_edge_rate("SPEAKER_B") == EDGE_RATE_SPEAKER_B
    assert EDGE_RATE_SPEAKER_A != "+0%"
    q_pitch = living_uk_edge_pitch("Долар?", "SPEAKER_B")
    assert "Hz" in q_pitch
    assert q_pitch != "+3Hz"

    segs = [{"index": 0, "text": "Привіт", "start_ms": 0, "end_ms": 1000}]
    stamp = lock_simple_pipeline_voice(
        segs,
        pipeline_voice="uk-UA-OstapNeural",
        task_info={"target_lang": "uk", "simple_pipeline": True},
    )
    assert segs[0]["voice"] == "mykyta"
    assert stamp["pipeline_voice"] == "mykyta"


def test_stage40_pad_census_still_holds(tmp_path, monkeypatch):
    import api.auto_dub_api as api
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    monkeypatch.setattr(
        api,
        "_repair_missing_tts_files",
        lambda *a, **k: {"repaired": 0, "padded": 0, "failed": 0},
    )
    session = tmp_path / "session"
    tid = "stage41pad"
    seg = {
        "segment_id": "hole0",
        "index": 0,
        "text": "Привіт",
        "final_tts_text": "Привіт",
        "start_ms": 0,
        "end_ms": 1200,
        "slot_ms": 1200,
        "tts_ms": 0,
        "file": None,
        "speaker_id": "SPEAKER_A",
    }
    info = {
        "task_id": tid,
        "session_dir": str(session),
        "target_lang": "uk",
        "simple_pipeline": True,
        "happy_path": True,
    }
    out = api._prepare_segments_audio_before_mux(
        [seg],
        task_info=info,
        task_id=tid,
        timing_map=None,
        voice="uk-UA-OstapNeural",
    )
    assert out["ok"] is True
    block = _build_openddf_tts_pipeline_block(info, segments_data=[seg])
    assert block["audio_missing"] == 0
    assert block["final_status"] in ("ok", "ok_with_pads")
    assert "speakers" in block
    assert "prosody" in block
    assert "text_quality" in block
