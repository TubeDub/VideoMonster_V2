# -*- coding: utf-8 -*-
"""Stage 19c: production text-fit — mandatory re-TTS, split, no AudioOnly sole path."""

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


def test_underflow_737_text_slot_fit(tmp_path: Path):
    from engines.closed_loop_timing import (
        TEXT_FIT_DELTA_MS,
        apply_stage19b_rule_text_fit,
    )

    assert TEXT_FIT_DELTA_MS == 350
    slot_ms = 5000
    tts_ms = slot_ms - 737
    seg = {
        "plain_text": "Тож він пішов.",
        "text": "Тож він пішов.",
        "file": str(tmp_path / "u.wav"),
        "tts_ms": tts_ms,
        "playback_duration": tts_ms,
        "actual_duration_ms": tts_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "first_tts_duration_ms": tts_ms,
    }
    (tmp_path / "u.wav").write_bytes(b"RIFF")
    timing_map = [{"start": 0, "end": slot_ms}]

    def _fake_budget(seg, idx, timing_map):
        from engines.closed_loop_timing import TimingBudget

        measured = int(seg.get("playback_duration") or seg.get("tts_ms") or 0)
        slot = slot_ms
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
        )

    def regen(text, **kwargs):
        out = tmp_path / "u_regen.wav"
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
    ):
        budget = _fake_budget(seg, 0, timing_map)
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
    assert str(seg.get("adaptation_skip_reason") or "") != "FitsNoChange"
    algo = str(seg.get("algorithm_reason") or "")
    assert "AudioStrategyNoTextRewrite" not in algo
    assert "TextSlotFit" in algo or "TextThenAtemo" in algo
    assert bool(seg.get("algorithm_reason_locked"))
    assert seg.get("stage19c") is not None
    assert (
        bool(seg.get("expand_executed"))
        or bool(seg.get("rule_rewrite_used"))
        or float(seg.get("fill_ratio") or 0) >= 0.90
        or str(seg.get("strategy") or "") in ("atempo_slow", "expand_then_slow", "dead_air_risk")
    )


def test_overflow_20pct_shorten_retts(tmp_path: Path):
    from engines.closed_loop_timing import apply_stage19b_rule_text_fit

    slot_ms = 4000
    tts_ms = int(slot_ms * 1.20)
    long_uk = (
        "Він дуже довго розповідав про те, як саме тоді вирішив піти далі "
        "дорогою і нікого не чекати, бо час уже спливав надто швидко."
    )
    seg = {
        "plain_text": long_uk,
        "text": long_uk,
        "file": str(tmp_path / "o.wav"),
        "tts_ms": tts_ms,
        "playback_duration": tts_ms,
        "actual_duration_ms": tts_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "first_tts_duration_ms": tts_ms,
    }
    (tmp_path / "o.wav").write_bytes(b"RIFF")
    timing_map = [{"start": 0, "end": slot_ms}]
    regen_calls = {"n": 0}

    def _fake_budget(seg, idx, timing_map):
        from engines.closed_loop_timing import TimingBudget

        measured = int(seg.get("playback_duration") or seg.get("tts_ms") or 0)
        delta = measured - slot_ms
        return TimingBudget(
            index=idx,
            slot_start=0,
            slot_end=slot_ms,
            slot_duration=slot_ms,
            tts_duration=measured,
            measured_duration=measured,
            delta=delta,
            overflow=max(0, delta),
            underflow=max(0, -delta),
            status="overflow" if delta > 100 else "ok",
        )

    def regen(text, **kwargs):
        regen_calls["n"] += 1
        out = tmp_path / f"o_regen_{regen_calls['n']}.wav"
        out.write_bytes(b"RIFF")
        return str(out), max(slot_ms - 50, int(len(text) * 30))

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
    if seg.get("rule_rewrite_used"):
        assert regen_calls["n"] >= 1
        assert seg.get("final_tts_text")
        assert int(budget.rewrite_iterations or 0) >= 1


def test_large_overflow_split_18s():
    from engines.closed_loop_timing import (
        large_overflow_needs_split,
        try_stage19c_overflow_split,
    )

    slot_ms = 8000
    overflow_ms = 18000
    assert large_overflow_needs_split(overflow_ms=overflow_ms, slot_ms=slot_ms) is True
    assert large_overflow_needs_split(overflow_ms=500, slot_ms=slot_ms) is False

    en = (
        "He walked for a long time. Then he stopped near the river. "
        "Finally he decided to go home before dark."
    )
    uk = (
        "Він довго йшов. Потім він зупинився біля річки. "
        "Зрештою він вирішив піти додому до темряви."
    )
    seg = {
        "plain_text": uk,
        "text": uk,
        "final_text": uk,
        "tts_ms": slot_ms + overflow_ms,
        "playback_duration": slot_ms + overflow_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "segment_id": "parent-1",
    }
    segments = [seg]
    sources = [en]
    timing = [{"start": 0, "end": slot_ms}]
    ok = try_stage19c_overflow_split(
        segments_data=segments,
        source_segments=sources,
        timing_map=timing,
        audits=None,
        idx=0,
        overflow_ms=overflow_ms,
    )
    assert ok is True
    assert len(segments) >= 2
    for s in segments:
        assert s.get("algorithm_reason") == "TextSlotSplit"
        assert s.get("strategy") == "split"
        assert int(s.get("slot_ms") or 0) >= 800 or len(segments) == 2
    # Parent overflow distributed — no single child keeps full 18s delta as sole plan.
    assert all(s.get("stage19c_split_done") for s in segments)


def test_text_changed_regen_none_fail_loud(tmp_path: Path):
    from engines.closed_loop_timing import (
        TextFitNoRegenError,
        apply_stage19b_rule_text_fit,
    )

    slot_ms = 5000
    tts_ms = 2000
    seg = {
        "plain_text": "Він пішов.",
        "text": "Він пішов.",
        "file": str(tmp_path / "nr.wav"),
        "tts_ms": tts_ms,
        "playback_duration": tts_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "first_tts_duration_ms": tts_ms,
    }
    (tmp_path / "nr.wav").write_bytes(b"RIFF")
    timing_map = [{"start": 0, "end": slot_ms}]

    def _fake_budget(seg, idx, timing_map):
        from engines.closed_loop_timing import TimingBudget

        measured = int(seg.get("playback_duration") or 0)
        delta = measured - slot_ms
        return TimingBudget(
            index=idx,
            slot_start=0,
            slot_end=slot_ms,
            slot_duration=slot_ms,
            tts_duration=measured,
            measured_duration=measured,
            delta=delta,
            overflow=max(0, delta),
            underflow=max(0, -delta),
            status="underflow",
        )

    with patch(
        "engines.closed_loop_timing.build_timing_budget", side_effect=_fake_budget
    ), patch(
        "engines.closed_loop_timing.apply_dynamic_pause_engine",
        return_value={"applied": False},
    ), patch(
        "engines.text_slot_fit.fit_text_to_slot",
    ) as fit_mock:
        class _Fit:
            text = "Тож тоді він справді пішов далі дорогою."
            changed = True
            action = "expand"
            strategy = "expand"
            fill_ratio = 0.5
            atempo = 0.9
            reasons = ["rule_expand"]

        fit_mock.return_value = _Fit()
        budget = _fake_budget(seg, 0, timing_map)
        with pytest.raises(TextFitNoRegenError) as ei:
            apply_stage19b_rule_text_fit(
                seg,
                0,
                timing_map,
                budget,
                source_hint="So he walked.",
                target_lang="uk",
                voice="uk-UA-OstapNeural",
                work_dir=tmp_path,
                regen_fn=None,
            )
        assert "PIPELINE_TEXT_FIT_NO_REGEN" in str(ei.value)
    assert "AudioStrategyNoTextRewrite" not in str(seg.get("algorithm_reason") or "")


def test_algorithm_reason_not_audio_only_after_lock():
    from engines.pipeline_integrity.honest_diagnostics import apply_honest_reasons

    seg = {
        "algorithm_reason": "TextSlotFitExpand",
        "algorithm_reason_locked": True,
        "text_adaptation_reason": "TextSlotFitExpand",
        "rule_rewrite_used": True,
        "expand_executed": True,
        "text_adaptation_trace": {
            "executed": True,
            "stages": ["text_slot_fit:expand"],
            "reasons": ["TextSlotFitExpand"],
        },
        "slot_ms": 5000,
        "tts_ms": 4800,
        "playback_duration": 4800,
        "adaptation_stages": ["text_slot_fit:expand"],
    }
    apply_honest_reasons(seg)
    assert seg["algorithm_reason"] == "TextSlotFitExpand"
    assert "AudioStrategyNoTextRewrite" not in str(seg.get("algorithm_reason") or "")
