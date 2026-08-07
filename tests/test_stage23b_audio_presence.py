# -*- coding: utf-8 -*-
"""Stage 23b: audio must exist after slot_fit / repair; duration control forced."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_audio_stat_rejects_tiny_and_missing():
    from engines.pipeline_integrity.audio_presence import MIN_AUDIO_BYTES, audio_stat

    assert MIN_AUDIO_BYTES == 1000
    ok, size = audio_stat("/nonexistent/path.wav")
    assert ok is False and size == 0

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        tiny = base / "tiny.wav"
        tiny.write_bytes(b"x" * 100)
        ok, size = audio_stat(tiny)
        assert ok is False and size == 100

        good = base / "good.wav"
        good.write_bytes(b"y" * 1500)
        ok, size = audio_stat(good)
        assert ok is True and size == 1500


def test_segment_needs_audio_repair_for_ghost_and_split_child():
    from engines.pipeline_integrity.audio_presence import segment_needs_audio_repair

    assert (
        segment_needs_audio_repair(
            {"text": "Привіт", "final_tts_text": "Привіт", "file": None}
        )
        is True
    )
    assert (
        segment_needs_audio_repair(
            {
                "text": "Привіт",
                "final_tts_text": "Привіт",
                "file": "ghost.wav",
                "tts_ms": 0,
                "needs_re_tts": True,
                "split_child": True,
            }
        )
        is True
    )
    assert (
        segment_needs_audio_repair({"text": "x", "merged_into": 1, "file": None})
        is False
    )


def test_needs_stage23_fill_on_short_slot_underfill():
    from engines.closed_loop_timing import TimingBudget, _needs_stage23_fill

    # fill=0.80 on 1000ms — |Δ|<350 but Stage23 must fire
    b = TimingBudget(
        index=0,
        slot_duration=1000,
        measured_duration=800,
        original_duration=800,
        underflow=200,
        overflow=0,
        final_status="ok",
        delta=-200,
    )
    assert _needs_stage23_fill(b) is True

    ok = TimingBudget(
        index=0,
        slot_duration=1000,
        measured_duration=950,
        original_duration=950,
        underflow=50,
        overflow=0,
        final_status="ok",
        delta=-50,
    )
    assert _needs_stage23_fill(ok) is False


def test_duration_control_forced_sets_used():
    from engines.closed_loop_timing import (
        TimingBudget,
        _apply_stage23_duration_control,
    )
    from engines.tts_backends import set_pipeline_tts_backend

    set_pipeline_tts_backend("tts_uk")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out_stage23b.wav"
        out.write_bytes(b"RIFF" + b"\x00" * 1200)
        calls: list[dict] = []

        def fake_regen(text, **kwargs):
            calls.append(kwargs)
            return (str(out), 960)

        seg = {
            "text": "Тест тривалості під слот.",
            "final_tts_text": "Тест тривалості під слот.",
            "tts_backend": "tts_uk",
            "tts_voice": "mykyta",
            "tts_rate": 0.97,
            "tts_length_scale": 1.05,
            "file": "in.wav",
        }
        budget = TimingBudget(
            index=0,
            slot_duration=2000,
            measured_duration=1200,
            original_duration=1200,
            underflow=800,
            overflow=0,
            final_status="dead_air_risk",
            delta=-800,
        )
        timing_map = [{"start_ms": 0, "end_ms": 2000, "duration_ms": 2000}]
        new_b = _apply_stage23_duration_control(
            seg,
            0,
            timing_map,
            budget,
            voice="mykyta",
            work_dir=Path(td),
            regen_fn=fake_regen,
            commit_fn=None,
            tts_rate="0.97",
            tts_pitch="0",
            task_id="t23b",
            resolve_path=None,
        )
        assert calls, "must re-TTS"
        assert float(calls[0].get("length_scale") or 0) >= 1.05
        assert seg.get("duration_control_used") in ("length_scale", "rate")
        assert int(new_b.measured_duration or 0) == 960
    set_pipeline_tts_backend(None)


def test_stamp_audio_presence_on_stage23():
    from engines.pipeline_integrity.audio_presence import stamp_audio_presence

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "seg.wav"
        wav.write_bytes(b"z" * 2048)
        seg = {"file": str(wav), "stage23": {}}
        info = stamp_audio_presence(seg)
        assert info["ok"] is True
        assert seg["audio_exists"] is True
        assert seg["audio_size_bytes"] >= 1000
        assert seg["stage23"]["audio_exists"] is True


def test_runtime_tag_stage23b():
    from engines.closed_loop_timing import STAGE23_RUNTIME_TAG

    assert "stage23b" in STAGE23_RUNTIME_TAG


if __name__ == "__main__":
    test_audio_stat_rejects_tiny_and_missing()
    test_segment_needs_audio_repair_for_ghost_and_split_child()
    test_needs_stage23_fill_on_short_slot_underfill()
    test_stamp_audio_presence_on_stage23()
    test_duration_control_forced_sets_used()
    test_runtime_tag_stage23b()
    print("ALL_GREEN")
