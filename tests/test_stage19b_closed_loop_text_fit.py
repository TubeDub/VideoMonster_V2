# -*- coding: utf-8 -*-
"""Stage 19b: closed-loop text fit before tempo (no AudioOnly / FitsNoChange)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_forbid_fast_then_gap_1_2_0_7():
    from engines.text_slot_fit import forbid_fast_then_gap

    assert forbid_fast_then_gap(1.2, 0.7) is True


def test_underflow_737_not_fits_no_change(tmp_path: Path):
    """underflow 737 ms → expand_executed or atempo_slow; never FitsNoChange."""
    from engines.closed_loop_timing import (
        TEXT_FIT_DELTA_MS,
        apply_stage19b_rule_text_fit,
        build_timing_budget,
    )

    assert TEXT_FIT_DELTA_MS == 350
    slot_ms = 5000
    tts_ms = slot_ms - 737  # underflow 737
    seg = {
        "plain_text": "Тож він пішов.",
        "text": "Тож він пішов.",
        "file": str(tmp_path / "dummy.wav"),
        "tts_ms": tts_ms,
        "playback_duration": tts_ms,
        "actual_duration_ms": tts_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "first_tts_duration_ms": tts_ms,
    }
    (tmp_path / "dummy.wav").write_bytes(b"RIFF")

    timing_map = [{"start_ms": 0, "end_ms": slot_ms, "duration_ms": slot_ms}]

    def _fake_budget(seg, idx, timing_map):
        from engines.closed_loop_timing import TimingBudget

        measured = int(seg.get("playback_duration") or seg.get("tts_ms") or 0)
        slot = int(timing_map[0]["duration_ms"])
        delta = measured - slot
        return TimingBudget(
            index=idx,
            slot_start=0,
            slot_end=slot,
            slot_duration=slot,
            tts_duration=measured,
            measured_duration=measured,
            delta=delta,
            overflow=max(0, delta),
            underflow=max(0, -delta),
            status="underflow" if delta < -350 else ("overflow" if delta > 100 else "ok"),
        )

    regenerated = {"n": 0}

    def regen(text, **kwargs):
        regenerated["n"] += 1
        # Longer text → pretend longer TTS closer to slot.
        new_ms = min(slot_ms, max(tts_ms + 400, int(len(text) * 40)))
        out = tmp_path / f"regen_{regenerated['n']}.wav"
        out.write_bytes(b"RIFF")
        return str(out), new_ms

    with patch(
        "engines.closed_loop_timing.build_timing_budget", side_effect=_fake_budget
    ), patch(
        "engines.closed_loop_timing.measure_actual_ms",
        side_effect=lambda s, **k: int(s.get("playback_duration") or s.get("tts_ms") or 0),
    ), patch(
        "engines.closed_loop_timing.apply_dynamic_pause_engine",
        return_value={"applied": False},
    ), patch(
        "engines.closed_loop_timing._apply_light_atempo_after_fit",
        side_effect=lambda seg, budget, **k: budget,
    ):
        budget = _fake_budget(seg, 0, timing_map)
        assert budget.underflow == 737
        budget, attempted = apply_stage19b_rule_text_fit(
            seg,
            0,
            timing_map,
            budget,
            source_hint="So he kept walking down that long road for a while then.",
            target_lang="uk",
            voice="uk-UA-OstapNeural",
            work_dir=tmp_path,
            regen_fn=regen,
        )

    assert attempted is True
    skip = str(seg.get("adaptation_skip_reason") or "")
    assert skip != "FitsNoChange"
    assert (
        bool(seg.get("expand_executed"))
        or bool(seg.get("rule_rewrite_used"))
        or float(seg.get("fill_ratio") or 0) >= 0.90
        or str(seg.get("strategy") or "") in ("atempo_slow", "expand_then_slow", "dead_air_risk")
    )
    algo = str(seg.get("algorithm_reason") or "")
    assert "TextSlotFit" in algo or "TextThenAtemo" in algo
    assert "AudioStrategyNoTextRewrite" not in algo
    if seg.get("rule_rewrite_used"):
        assert regenerated["n"] >= 1
        assert int(budget.rewrite_iterations or 0) >= 1


def test_overflow_shorten_not_tempo_only(tmp_path: Path):
    """overflow >15% slot → shorten/paraphrase attempt; not audio-only sole answer."""
    from engines.closed_loop_timing import apply_stage19b_rule_text_fit

    slot_ms = 4000
    tts_ms = int(slot_ms * 1.25)  # 25% overflow
    long_uk = (
        "Він дуже довго розповідав про те, як саме тоді вирішив піти далі "
        "дорогою і нікого не чекати, бо час уже спливав надто швидко."
    )
    seg = {
        "plain_text": long_uk,
        "text": long_uk,
        "file": str(tmp_path / "ov.wav"),
        "tts_ms": tts_ms,
        "playback_duration": tts_ms,
        "actual_duration_ms": tts_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "first_tts_duration_ms": tts_ms,
    }
    (tmp_path / "ov.wav").write_bytes(b"RIFF")
    timing_map = [{"start_ms": 0, "end_ms": slot_ms, "duration_ms": slot_ms}]

    def _fake_budget(seg, idx, timing_map):
        from engines.closed_loop_timing import TimingBudget

        measured = int(seg.get("playback_duration") or seg.get("tts_ms") or 0)
        slot = int(timing_map[0]["duration_ms"])
        delta = measured - slot
        return TimingBudget(
            index=idx,
            slot_start=0,
            slot_end=slot,
            slot_duration=slot,
            tts_duration=measured,
            measured_duration=measured,
            delta=delta,
            overflow=max(0, delta),
            underflow=max(0, -delta),
            status="overflow" if delta > 100 else "ok",
        )

    def regen(text, **kwargs):
        new_ms = max(slot_ms - 100, int(len(text) * 35))
        out = tmp_path / "ov_regen.wav"
        out.write_bytes(b"RIFF")
        return str(out), new_ms

    with patch(
        "engines.closed_loop_timing.build_timing_budget", side_effect=_fake_budget
    ), patch(
        "engines.closed_loop_timing.measure_actual_ms",
        side_effect=lambda s, **k: int(s.get("playback_duration") or s.get("tts_ms") or 0),
    ), patch(
        "engines.closed_loop_timing.apply_dynamic_pause_engine",
        return_value={"applied": False},
    ), patch(
        "engines.closed_loop_timing._apply_light_atempo_after_fit",
        side_effect=lambda seg, budget, **k: budget,
    ):
        budget = _fake_budget(seg, 0, timing_map)
        assert budget.overflow > int(slot_ms * 0.15)
        budget, attempted = apply_stage19b_rule_text_fit(
            seg,
            0,
            timing_map,
            budget,
            source_hint="He talked for a long time about how he decided to keep going.",
            target_lang="uk",
            voice="uk-UA-OstapNeural",
            work_dir=tmp_path,
            regen_fn=regen,
        )

    assert attempted is True
    algo = str(seg.get("algorithm_reason") or "")
    assert "AudioStrategyNoTextRewrite" not in algo
    # Either text shortened, or documented atempo path after fit attempt.
    assert (
        bool(seg.get("rule_rewrite_used"))
        or "TextSlotFit" in algo
        or "TextThenAtemo" in algo
    )
    assert str(seg.get("adaptation_skip_reason") or "") != "FitsNoChange"


def test_closed_loop_max_iters_zero_still_text_fits(tmp_path: Path):
    """Happy Path max_iterations=0 must still run Stage 19b (not pause_only only)."""
    from engines.closed_loop_timing import run_closed_loop_segment

    slot_ms = 6000
    tts_ms = 6000 - 800
    seg = {
        "plain_text": "Він пішов.",
        "text": "Він пішов.",
        "file": str(tmp_path / "hp.wav"),
        "tts_ms": tts_ms,
        "playback_duration": tts_ms,
        "actual_duration_ms": tts_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "first_tts_duration_ms": tts_ms,
    }
    (tmp_path / "hp.wav").write_bytes(b"RIFF")
    timing_map = [{"start_ms": 0, "end_ms": slot_ms, "duration_ms": slot_ms}]

    def _fake_budget(seg, idx, timing_map):
        from engines.closed_loop_timing import TimingBudget

        measured = int(seg.get("playback_duration") or seg.get("tts_ms") or 0)
        slot = int(timing_map[0]["duration_ms"])
        delta = measured - slot
        return TimingBudget(
            index=idx,
            slot_start=0,
            slot_end=slot,
            slot_duration=slot,
            tts_duration=measured,
            measured_duration=measured,
            delta=delta,
            overflow=max(0, delta),
            underflow=max(0, -delta),
            status="underflow" if delta < -350 else "ok",
            original_duration=measured,
        )

    def regen(text, **kwargs):
        out = tmp_path / "hp_regen.wav"
        out.write_bytes(b"RIFF")
        return str(out), min(slot_ms, tts_ms + 500)

    with patch(
        "engines.closed_loop_timing.build_timing_budget", side_effect=_fake_budget
    ), patch(
        "engines.closed_loop_timing.measure_actual_ms",
        side_effect=lambda s, **k: int(s.get("playback_duration") or s.get("tts_ms") or 0),
    ), patch(
        "engines.closed_loop_timing.apply_dynamic_pause_engine",
        return_value={"applied": False},
    ), patch(
        "engines.closed_loop_timing._apply_light_atempo_after_fit",
        side_effect=lambda seg, budget, **k: budget,
    ), patch(
        "engines.closed_loop_timing.stamp_need_adaptation_gate",
        create=True,
    ):
        # stamp is imported inside function — patch adaptation_decision
        with patch(
            "engines.dub_engine_v2.adaptation_decision.stamp_need_adaptation_gate",
            return_value=True,
        ):
            budget = run_closed_loop_segment(
                seg,
                0,
                timing_map,
                source_hint="He walked for a long time along the road.",
                target_lang="uk",
                src_lang="en",
                voice="uk-UA-OstapNeural",
                work_dir=tmp_path,
                regen_fn=regen,
                max_iterations=0,
            )

    assert budget.rewrite_reason != "pause_only_after_resegment" or bool(
        seg.get("expand_executed") or seg.get("rule_rewrite_used")
    )
    assert str(seg.get("adaptation_skip_reason") or "") != "FitsNoChange"
    algo = str(seg.get("algorithm_reason") or "")
    if abs(int(budget.delta or 0)) > 350 or int(budget.underflow or 0) > 350:
        assert "TextSlotFit" in algo or "TextThenAtemo" in algo or bool(
            seg.get("expand_required")
        )


def test_algorithm_reason_text_slot_fit_on_large_delta():
    from engines.closed_loop_timing import _stage19b_algorithm_reason

    class _Fit:
        action = "expand"
        strategy = "expand"

    assert _stage19b_algorithm_reason(_Fit(), text_changed=True) == "TextSlotFitExpand"

    class _Fit2:
        action = "shorten"
        strategy = "shorten"

    assert _stage19b_algorithm_reason(_Fit2(), text_changed=True) == "TextSlotFitShorten"
