# -*- coding: utf-8 -*-
"""Stage 31 — text-first duration, ±8% tempo, evenness, franchise ending.

Gaps after Stage 30 this suite locks:
- length_scale/atempo ran before text-fit (sharp accelerate)
- clamps allowed 1.18 / 1.20 neighbor jumps
- shorten could drop Fiat / USC / Wexler / Star Wars
- last lines could lose franchise / Lucas meaning
- census degraded while audio_missing==0 (already Stage 30; keep the lock)
"""

from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

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


def test_text_fit_before_atempo_when_delta_gt_120():
    from engines.text_slot_fit import (
        STAGE31_SPEED_DELTA_MS,
        STAGE31_TEXT_FIT_DELTA_MS,
        stage31_duration_levers,
    )

    assert STAGE31_TEXT_FIT_DELTA_MS == 120
    assert STAGE31_SPEED_DELTA_MS == 150
    overflow = stage31_duration_levers(slot_ms=3000, tts_ms=5000)
    assert overflow[0] == "text_shorten"
    assert overflow.index("text_shorten") < overflow.index("length_scale")
    assert overflow.index("length_scale") < overflow.index("atempo")
    under = stage31_duration_levers(slot_ms=5000, tts_ms=3000)
    assert under[0] == "text_expand"
    assert "atempo" in under
    tiny = stage31_duration_levers(slot_ms=3000, tts_ms=3080)
    assert tiny == []


def test_apply_stage19b_regens_text_before_length_scale():
    from engines.closed_loop_timing import TimingBudget, apply_stage19b_rule_text_fit
    from engines.text_slot_fit import TextFitResult
    from engines.tts_backends import set_pipeline_tts_backend

    set_pipeline_tts_backend("tts_uk")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "fake_out.wav"
        out.write_bytes(b"RIFF" + b"\x00" * 1600)
        calls: list[dict] = []

        def fake_regen(text, **kwargs):
            calls.append(
                {
                    "text": text,
                    "length_scale": kwargs.get("length_scale"),
                    "mykyta": kwargs.get("mykyta_controls"),
                }
            )
            return (str(out), 3050)

        fit = TextFitResult(
            text="Джордж купив старий Фіат.",
            slot_ms=3000,
            predicted_ms_before=5000,
            predicted_ms_after=3100,
            action="shorten",
            changed=True,
            reasons=["safe_shorten"],
            meaning_preserved=True,
            fill_ratio=1.03,
            atempo=1.0,
            strategy="shorten",
            predicted_tts_ms=3100,
        )
        seg = {
            "text": "Отже, власне кажучи, Джордж молодший купив старий Фіат.",
            "final_tts_text": "Отже, власне кажучи, Джордж молодший купив старий Фіат.",
            "tts_backend": "tts_uk",
            "tts_voice": "mykyta",
            "tts_rate": 0.97,
            "tts_length_scale": 1.05,
            "file": str(out),
            "split_children": 2,
            "stage19e_split_done": True,
        }
        budget = TimingBudget(
            index=0,
            slot_duration=3000,
            measured_duration=5000,
            original_duration=5000,
            underflow=0,
            overflow=2000,
            final_status="overflow",
            delta=2000,
        )
        timing_map = [{"start_ms": 0, "end_ms": 3000, "duration_ms": 3000}]
        with patch("engines.text_slot_fit.fit_text_to_slot", return_value=fit):
            apply_stage19b_rule_text_fit(
                seg,
                0,
                timing_map,
                budget,
                source_hint="George Jr bought an old Fiat.",
                target_lang="uk",
                voice="mykyta",
                work_dir=Path(td),
                regen_fn=fake_regen,
                commit_fn=None,
                tts_rate="0.97",
                tts_pitch="0",
                task_id="t31",
                resolve_path=None,
            )
        assert calls, "text-first path must re-TTS"
        assert calls[0].get("length_scale") in (None, "")
        assert calls[0].get("mykyta") in (None, {})
        assert "Фіат" in calls[0]["text"]
        used = str(seg.get("duration_control_used") or "")
        assert used in ("text_shorten", "length_scale", "atempo", "text_expand")
        assert used != "none"
    set_pipeline_tts_backend(None)


def test_length_scale_atempo_clamped_092_108():
    from engines.conflict_resolver import STAGE24_ATEMPO_CLAMP_MAX, STAGE24_ATEMPO_CLAMP_MIN
    from engines.text_slot_fit import (
        STAGE31_ATEMPO_MAX,
        STAGE31_ATEMPO_MIN,
        clamp_stage31_tempo,
    )
    from engines.tts_backends import (
        MYKYTA_DURATION_LENGTH_SCALE_RANGE,
        MYKYTA_DURATION_RATE_RANGE,
        compute_mykyta_duration_controls,
    )

    assert MYKYTA_DURATION_LENGTH_SCALE_RANGE == (0.92, 1.08)
    assert MYKYTA_DURATION_RATE_RANGE == (0.92, 1.08)
    assert STAGE24_ATEMPO_CLAMP_MIN == 0.92
    assert STAGE24_ATEMPO_CLAMP_MAX == 1.08
    assert STAGE31_ATEMPO_MIN == 0.92
    assert STAGE31_ATEMPO_MAX == 1.08

    stretch = compute_mykyta_duration_controls(10000, 7000)
    assert 0.92 <= stretch["length_scale"] <= 1.08
    assert 0.92 <= stretch["rate"] <= 1.08
    compress = compute_mykyta_duration_controls(7000, 10000)
    assert 0.92 <= compress["length_scale"] <= 1.08
    assert 0.92 <= compress["rate"] <= 1.08
    assert clamp_stage31_tempo(1.20) == 1.08
    assert clamp_stage31_tempo(0.80) == 0.92


def test_neighbor_tempo_evenness_no_095_then_120():
    from engines.closed_loop_timing import equalize_segment_tempos

    segs = [
        {"index": 0, "atempo": 0.95, "tts_length_scale": 1.0},
        {"index": 1, "atempo": 1.20, "tts_length_scale": 1.0},
        {"index": 2, "atempo": 1.00, "tts_length_scale": 1.0},
    ]
    stats = equalize_segment_tempos(segs)
    assert stats["adjusted"] >= 1
    values = [float(s["atempo"]) for s in segs]
    assert all(0.92 <= v <= 1.08 for v in values)
    for a, b in zip(values, values[1:]):
        assert abs(a - b) <= 0.08 + 1e-9, (values, a, b)
    assert all(0.92 <= float(s["tts_length_scale"]) <= 1.08 for s in segs)


def test_entities_preserved_in_shorten():
    from engines.text_slot_fit import shorten_preserving_entities

    text = (
        "Отже, власне кажучи, Джордж молодший, ну, купив старий Фіат, дійсно, "
        "і вступив до USC, насправді, а Хаскелл Векслер зняв Зоряні війни, практично."
    )
    hint = "George Jr Fiat USC Haskell Wexler Star Wars franchise"
    out, reasons, _truncated = shorten_preserving_entities(
        text, slot_ms=1200, lang="uk", source_hint=hint
    )
    blob = out.lower()
    assert "фіат" in blob or "fiat" in blob, out
    assert "usc" in blob, out
    assert "векслер" in blob or "wexler" in blob, out
    assert "зоряні" in blob or "star wars" in blob or "війни" in blob, out
    assert "джордж" in blob or "george" in blob, out
    assert "shorten_refused_protected_entity" not in reasons or out == " ".join(text.split())


def test_last_segments_keep_franchise_lucas_idea():
    from engines.text_slot_fit import (
        ending_keeps_franchise_idea,
        restore_ending_franchise_meaning,
    )

    src = (
        "Today he is known as George Lucas, and his franchise became Star Wars, "
        "one of the most successful films of all time."
    )
    dropped = "Сьогодні він відомий."
    assert ending_keeps_franchise_idea(src, dropped) is False
    restored = restore_ending_franchise_meaning(dropped, src)
    assert ending_keeps_franchise_idea(src, restored) is True
    low = restored.lower()
    assert "лукас" in low
    assert "зоряні" in low or "франшиз" in low
    # Vacuous when source has no franchise idea.
    assert ending_keeps_franchise_idea("He bought a camera.", "Він купив камеру.") is True


def test_census_missing_zero_after_last_resort_not_degraded(tmp_path):
    from api.auto_dub_api import _last_resort_pad_missing_segments
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    session_dir = tmp_path / "session"
    tid = "stage31pad"
    segs = [
        {
            "segment_id": "end0",
            "index": 0,
            "text": "Кінофраншиза — це Зоряні війни.",
            "final_tts_text": "Кінофраншиза — це Зоряні війни.",
            "start_ms": 0,
            "end_ms": 1000,
            "slot_ms": 1000,
            "tts_ms": 0,
            "file": None,
        },
        {
            "segment_id": "end1",
            "index": 1,
            "text": "Джордж Лукас.",
            "final_tts_text": "Джордж Лукас.",
            "start_ms": 1100,
            "end_ms": 2200,
            "slot_ms": 1100,
            "tts_ms": 0,
            "file": None,
        },
    ]
    task_info = {
        "task_id": tid,
        "session_dir": str(session_dir),
        "target_lang": "uk",
        "padded_count": 0,
        "final_status": "degraded",
    }
    stats = _last_resort_pad_missing_segments(
        segs, task_info=task_info, task_id=tid, timing_map=None
    )
    assert stats["padded_count"] >= 1
    block = _build_openddf_tts_pipeline_block(task_info, segments_data=segs)
    assert block["audio_missing"] == 0, block
    assert block.get("final_status") != "degraded"
    assert block.get("final_status") in ("ok", "ok_with_pads", None) or int(
        block.get("padded_count") or 0
    ) >= 1
    if int(block.get("audio_missing") or 0) == 0:
        assert str(block.get("final_status") or "ok") != "degraded"


def test_duration_control_used_not_none_when_speed_changed():
    from engines.closed_loop_timing import TimingBudget, _stamp_stage19e_fields

    seg = {
        "text": "Тест.",
        "final_tts_text": "Тест.",
        "tts_backend": "tts_uk",
        "tts_voice": "mykyta",
        "tts_length_scale": 1.08,
        "atempo": 1.06,
        "duration_control_used": "none",
    }
    budget = TimingBudget(
        index=0,
        slot_duration=4000,
        measured_duration=3000,
        original_duration=3000,
        underflow=1000,
        overflow=0,
        final_status="ok",
        delta=-1000,
    )
    _stamp_stage19e_fields(
        seg,
        budget=budget,
        algorithm_reason="TextThenAtemo",
        expand_executed=False,
        shorten_executed=False,
        split_executed=False,
    )
    used = str(seg.get("duration_control_used") or "")
    assert used != "none"
    assert used in ("length_scale", "atempo")
