# -*- coding: utf-8 -*-
"""Stage 23: Mykyta duration control + aggressive clean expand + overlap kill."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_stage23_fill_and_underflow_constants():
    from engines.closed_loop_timing import UNDERFLOW_TRIGGER_MS
    from engines.text_slot_fit import (
        MAX_SOFT_PADS_PER_SEG,
        STAGE23_OK_FILL_HI,
        STAGE23_OK_FILL_LO,
        UNDERFLOW_TRIGGER_MS as FIT_UNDERFLOW,
    )

    assert STAGE23_OK_FILL_LO == 0.92
    assert STAGE23_OK_FILL_HI == 1.12
    assert UNDERFLOW_TRIGGER_MS == 250
    assert FIT_UNDERFLOW == 250
    assert MAX_SOFT_PADS_PER_SEG == 2


def test_apply_stage19b_no_unboundlocal_on_underfill():
    """Regression: STAGE23_OK_FILL_LO must not be shadowed by a late import."""
    from pathlib import Path

    from engines.closed_loop_timing import (
        TextFitNoRegenError,
        TimingBudget,
        apply_stage19b_rule_text_fit,
    )

    seg = {
        "text": "Короткий текст.",
        "final_tts_text": "Короткий текст.",
        "tts_backend": "tts_uk",
        "tts_voice": "mykyta",
    }
    budget = TimingBudget(
        index=0,
        slot_duration=5000,
        measured_duration=3000,
        original_duration=3000,
        underflow=2000,
        overflow=0,
        final_status="dead_air_risk",
        delta=-2000,
    )
    timing_map = [{"start_ms": 0, "end_ms": 5000, "duration_ms": 5000}]
    try:
        apply_stage19b_rule_text_fit(
            seg,
            0,
            timing_map,
            budget,
            source_hint="",
            target_lang="uk",
            voice="mykyta",
            work_dir=Path("."),
            regen_fn=None,
        )
    except TextFitNoRegenError:
        pass  # expected without regen — proves we passed the needs_fit gate
    except UnboundLocalError as exc:  # pragma: no cover
        raise AssertionError(f"STAGE23_OK_FILL_LO shadowed: {exc}") from exc


def test_mykyta_production_defaults():
    from engines.tts_backends import (
        MYKYTA_LENGTH_SCALE_DEFAULT,
        MYKYTA_RATE_DEFAULT,
        MYKYTA_VOLUME_DEFAULT,
        resolve_mykyta_controls,
    )

    assert MYKYTA_RATE_DEFAULT == 0.97
    assert MYKYTA_LENGTH_SCALE_DEFAULT == 1.05
    assert MYKYTA_VOLUME_DEFAULT == 1.05
    ctrl = resolve_mykyta_controls({}, env=False)
    assert ctrl["rate"] == 0.97
    assert ctrl["length_scale"] == 1.05
    assert ctrl["volume"] == 1.05


def test_compute_mykyta_duration_controls():
    from engines.tts_backends import compute_mykyta_duration_controls

    ctrl = compute_mykyta_duration_controls(
        10000,
        8000,
        base={"rate": 0.97, "pitch": 0.0, "volume": 1.05, "length_scale": 1.05},
    )
    # slot/meas = 1.25 → clamp to 1.18
    assert ctrl["length_scale"] == 1.18
    assert 0.88 <= ctrl["rate"] <= 1.08
    # rate ≈ 1/1.18 ≈ 0.847 → clamp 0.88
    assert ctrl["rate"] == 0.88


def test_expand_allows_two_soft_pads_no_garbage():
    from engines.text_slot_fit import (
        SOFT_PAD_WHITELIST,
        expand_to_fill,
        estimate_tts_ms,
        is_garbage_expand,
        soft_pad_count,
    )

    base = "Камера була готова."
    pred = estimate_tts_ms(base, "uk")
    # Deep underfill so first pad alone cannot reach 0.92.
    slot = int(pred / 0.75)
    out, reasons = expand_to_fill(
        base,
        target_ms=slot,
        lang="uk",
        strategy_order=(
            "soft_pad_whitelist_once",
            "soft_pad_whitelist_twice",
        ),
    )
    assert is_garbage_expand(out) is False
    assert "Саме про" not in out
    assert soft_pad_count(out) <= 2
    if soft_pad_count(out) >= 1:
        assert any(p in out.lower() for p in SOFT_PAD_WHITELIST)
    assert "stage23:" in " ".join(reasons) or out != base or "already_filled" in " ".join(
        reasons
    )


def test_stage23_meta_stamped():
    from engines.closed_loop_timing import TimingBudget, _stamp_stage19e_fields

    seg = {
        "text": "Досить довгий текст щоб заповнити слот майже повністю без сміття.",
        "final_tts_text": "Досить довгий текст щоб заповнити слот майже повністю без сміття.",
        "tts_backend": "tts_uk",
        "tts_voice": "mykyta",
        "tts_rate": 0.97,
        "tts_pitch": 0.0,
        "tts_volume": 1.05,
        "tts_length_scale": 1.05,
        "duration_control_used": "length_scale",
        "overlap_after_ripple": 0,
    }
    budget = TimingBudget(
        index=0,
        slot_duration=10000,
        measured_duration=9500,
        original_duration=9000,
        underflow=500,
        overflow=0,
        final_status="ok",
        delta=-500,
    )
    _stamp_stage19e_fields(
        seg,
        budget=budget,
        algorithm_reason="TextSlotFitExpand",
        expand_executed=False,
        shorten_executed=False,
        split_executed=False,
    )
    meta = seg.get("stage23") or {}
    assert meta.get("duration_control_used") == "length_scale"
    assert meta.get("tts_length_scale") == 1.05
    assert meta.get("tts_rate") == 0.97
    assert 0.92 <= float(meta.get("fill_ratio") or 0) <= 1.12
    assert meta.get("final_status") == "ok"
    assert "soft_pad_count" in meta
    assert "overlap_after_ripple" in meta


def test_stage23_dead_air_below_092():
    from engines.closed_loop_timing import TimingBudget, _stamp_stage19e_fields

    seg = {
        "text": "Короткий.",
        "final_tts_text": "Короткий.",
        "tts_backend": "tts_uk",
        "tts_voice": "mykyta",
        "duration_control_used": "none",
    }
    budget = TimingBudget(
        index=0,
        slot_duration=5000,
        measured_duration=4000,  # fill 0.80
        original_duration=4000,
        underflow=1000,
        overflow=0,
        final_status="dead_air_risk",
        delta=-1000,
    )
    _stamp_stage19e_fields(
        seg,
        budget=budget,
        algorithm_reason="dead_air_risk",
        expand_executed=False,
        shorten_executed=False,
        split_executed=False,
    )
    meta = seg.get("stage23") or {}
    assert float(meta.get("fill_ratio") or 0) < 0.92
    assert meta.get("final_status") == "dead_air_risk"


def test_ripple_trigger_300_and_residual_count():
    from engines.conflict_resolver import (
        STAGE23_RIPPLE_OVERLAP_MS,
        ripple_shift_segment_dicts,
    )

    assert STAGE23_RIPPLE_OVERLAP_MS == 300
    segs = [
        {
            "index": 0,
            "start_time_ms": 0,
            "final_tts_duration_ms": 2000,
        },
        {
            "index": 1,
            "start_time_ms": 1650,  # overlap 350 > 300
            "final_tts_duration_ms": 800,
        },
    ]
    stats = ripple_shift_segment_dicts(segs)
    assert stats["ripple_shifted"] >= 1
    assert segs[1]["start_time_ms"] >= 2000
    assert int(stats.get("overlap_after_ripple") or 0) == 0


def test_duration_control_pipeline_sets_length_scale():
    from engines.closed_loop_timing import (
        TimingBudget,
        _apply_stage23_duration_control,
    )
    from engines.tts_backends import set_pipeline_tts_backend

    set_pipeline_tts_backend("tts_uk")
    calls: list[dict] = []

    def fake_regen(text, **kwargs):
        calls.append({"text": text, **kwargs})
        # Pretend length_scale stretch filled the slot.
        return ("fake_out.wav", 9600)

    seg = {
        "text": "Тестовий текст для контролю тривалості.",
        "final_tts_text": "Тестовий текст для контролю тривалості.",
        "tts_backend": "tts_uk",
        "tts_voice": "mykyta",
        "tts_rate": 0.97,
        "tts_length_scale": 1.05,
        "file": "in.wav",
    }
    budget = TimingBudget(
        index=0,
        slot_duration=10000,
        measured_duration=7000,
        original_duration=7000,
        underflow=3000,
        overflow=0,
        final_status="dead_air_risk",
        delta=-3000,
    )
    timing_map = [{"start_ms": 0, "end_ms": 10000, "duration_ms": 10000}]
    new_budget = _apply_stage23_duration_control(
        seg,
        0,
        timing_map,
        budget,
        voice="mykyta",
        work_dir=Path("."),
        regen_fn=fake_regen,
        commit_fn=None,
        tts_rate="0.97",
        tts_pitch="0",
        task_id="t23",
        resolve_path=None,
    )
    assert calls, "duration control must re-TTS"
    assert float(calls[0].get("length_scale") or 0) >= 1.05
    assert seg.get("duration_control_used") == "length_scale"
    assert int(new_budget.measured_duration or 0) == 9600
    set_pipeline_tts_backend(None)
