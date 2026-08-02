# -*- coding: utf-8 -*-
"""Stage 19d: anti-truncate + forced text fit before atempo."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_detect_silent_truncate():
    from engines.text_slot_fit import detect_silent_truncate

    raw = " ".join(["слово"] * 20)
    final = " ".join(["слово"] * 10)  # 50% < 75%
    assert detect_silent_truncate(final, raw, shorten_executed=False) is True
    assert detect_silent_truncate(final, raw, shorten_executed=True) is False


def test_anti_truncate_restores_and_needs_retts():
    from engines.closed_loop_timing import NeedReTTS, assert_no_silent_truncate

    raw = (
        "Джордж молодший зустрів Хаскелла Векслера і розповів про камеру "
        "та про те, як він любив кіно і Зоряні війни ще з дитинства."
    )
    truncated = "Джордж молодший зустрів Хаскелла."
    seg = {
        "raw_translation": raw,
        "semantic_engine_text": raw,
        "final_tts_text": truncated,
        "plain_text": truncated,
        "slot_ms": 8000,
        "shorten_executed": False,
    }
    with pytest.raises(NeedReTTS):
        assert_no_silent_truncate(seg, slot_ms=8000, lang="uk")
    assert seg.get("truncation_blocked") is True
    assert seg.get("shorten_executed") is True
    assert len(str(seg.get("final_tts_text") or "").split()) >= int(
        len(raw.split()) * 0.75
    )


def test_underfill_forced_expand_executed(tmp_path: Path):
    from engines.closed_loop_timing import apply_stage19b_rule_text_fit

    slot_ms = 8000
    tts_ms = slot_ms - 2800
    short = "Він пішов."
    raw = (
        "Він довго йшов тією дорогою і думав про те, як саме тоді "
        "вирішив змінити своє життя назавжди."
    )
    seg = {
        "plain_text": short,
        "text": short,
        "final_tts_text": short,
        "raw_translation": raw,
        "semantic_engine_text": raw,
        "file": str(tmp_path / "u.wav"),
        "tts_ms": tts_ms,
        "playback_duration": tts_ms,
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
            status="underflow" if delta < -350 else "ok",
        )

    def regen(text, **kwargs):
        out = tmp_path / "regen.wav"
        out.write_bytes(b"RIFF")
        return str(out), min(slot_ms, max(tts_ms + 800, len(text) * 35))

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
            source_hint="He walked a long road thinking about changing his life.",
            target_lang="uk",
            voice="uk-UA-OstapNeural",
            work_dir=tmp_path,
            regen_fn=regen,
        )

    assert attempted is True
    assert bool(seg.get("expand_executed")) is True or bool(seg.get("rule_rewrite_used"))
    algo = str(seg.get("algorithm_reason") or "")
    assert "AudioStrategyNoTextRewrite" not in algo
    # No bare TextThenAtemo without expand when underflow was large.
    if int(budget.underflow or 0) > 350 or abs(int(budget.delta or 0)) > 350:
        if algo == "TextThenAtemo":
            assert bool(seg.get("expand_executed") or seg.get("shorten_executed"))
    assert seg.get("stage19d") is not None
    assert "retention_score" in seg


def test_overflow_split_executed_flag():
    from engines.closed_loop_timing import (
        large_overflow_needs_split,
        try_stage19c_overflow_split,
    )

    assert large_overflow_needs_split(overflow_ms=5000, slot_ms=8000) is True
    uk = (
        "Він довго йшов. Потім він зупинився біля річки. "
        "Зрештою він вирішив піти додому до темряви. "
        "А ще він згадав Фіат і камеру."
    )
    en = (
        "He walked long. Then he stopped by the river. "
        "Finally he decided to go home. And he remembered the Fiat and camera."
    )
    seg = {
        "plain_text": uk,
        "text": uk,
        "final_text": uk,
        "tts_ms": 26000,
        "playback_duration": 26000,
        "slot_ms": 8000,
        "start_ms": 0,
        "end_ms": 8000,
        "segment_id": "p1",
    }
    segments = [seg]
    ok = try_stage19c_overflow_split(
        segments_data=segments,
        source_segments=[en],
        timing_map=[{"start": 0, "end": 8000}],
        audits=None,
        idx=0,
        overflow_ms=18000,
    )
    assert ok is True
    assert all(s.get("split_executed") for s in segments)


def test_bare_text_then_atemo_forbidden_on_large_underflow():
    from engines.closed_loop_timing import _stage19d_sanitize_algorithm_reason

    r = _stage19d_sanitize_algorithm_reason(
        "TextThenAtemo",
        expand_executed=False,
        shorten_executed=False,
        split_executed=False,
        delta_ms=-800,
        underflow_ms=800,
        overflow_ms=0,
    )
    assert r != "TextThenAtemo"
    # Stage 19f: do not claim TextSlotFitExpand when expand did not run.
    assert r != "TextSlotFitExpand"
    assert r == "dead_air_risk"
