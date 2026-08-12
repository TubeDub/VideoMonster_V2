# -*- coding: utf-8 -*-
"""Stage 22: smart expand (no garbage) + Mykyta controls + fill band."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_stage22_fill_constants():
    from engines.text_slot_fit import STAGE22_OK_FILL_HI, STAGE22_OK_FILL_LO

    assert STAGE22_OK_FILL_LO == 0.90
    assert STAGE22_OK_FILL_HI == 1.12


def test_expand_refuses_garbage_and_never_emits_same_pro():
    from engines.text_slot_fit import (
        expand_to_fill,
        is_garbage_expand,
        soft_pad_count,
        strip_garbage_expand_phrases,
    )

    dirty = "Він пішов далі. Саме про Джордж молодший тут ідеться."
    cleaned = strip_garbage_expand_phrases(dirty)
    assert "Саме про" not in cleaned
    assert is_garbage_expand(cleaned) is False

    out, reasons = expand_to_fill(
        dirty,
        target_ms=12000,
        lang="uk",
        source_hint="George Junior walked on.",
        raw_mt=cleaned,
        prefer_raw=cleaned,
    )
    assert "Саме про" not in out
    assert "тут ідеться" not in out.lower()
    assert is_garbage_expand(out) is False
    assert soft_pad_count(out) <= 2
    # Huge slot: either clean growth in band or honest refuse (no garbage).
    if out == cleaned or out == strip_garbage_expand_phrases(dirty):
        assert (
            "stage22:expand_refused" in reasons
            or "stage23:expand_refused" in reasons
            or "already_filled" in " ".join(reasons)
        )


def test_soft_pad_whitelist_once_only():
    from engines.text_slot_fit import (
        SOFT_PAD_WHITELIST,
        expand_to_fill,
        estimate_tts_ms,
        soft_pad_count,
    )

    # Pick a text whose estimate sits just under 0.90 of a slot that a single pad can fill.
    base = "Отже, камера була готова."
    pred = estimate_tts_ms(base, "uk")
    # Target slot so fill ≈ 0.88 (under 0.90) but pad can push into band.
    slot = int(pred / 0.88)
    out, reasons = expand_to_fill(
        base,
        target_ms=slot,
        lang="uk",
        strategy_order=("soft_pad_whitelist_once",),
    )
    assert soft_pad_count(out) <= 1
    if "stage22:soft_pad_whitelist_once" in reasons:
        assert soft_pad_count(out) == 1
        assert any(p in out.lower() for p in SOFT_PAD_WHITELIST)
        fr = estimate_tts_ms(out, "uk") / max(1, slot)
        assert 0.90 <= fr <= 1.12


def test_expand_already_filled_skips():
    from engines.text_slot_fit import expand_to_fill, estimate_tts_ms

    text = (
        "Джордж молодший завжди любив кіно і мріяв потрапити на знімальний майданчик "
        "разом із Хаскеллом Векслером і старим Фіатом."
    )
    pred = estimate_tts_ms(text, "uk")
    out, reasons = expand_to_fill(text, target_ms=max(1, int(pred / 0.95)), lang="uk")
    assert out == text
    assert "stage22:already_filled" in reasons


def test_mykyta_controls_clamp_and_stamp():
    from engines.tts_backends import (
        resolve_mykyta_controls,
        set_pipeline_mykyta_controls,
        stamp_tts_backend_meta,
    )

    ctrl = resolve_mykyta_controls(
        {"rate": 2.0, "pitch": -9, "volume": 0.1, "length_scale": 3.0}
    )
    assert ctrl["rate"] == 1.15
    assert ctrl["pitch"] == -4.0
    assert ctrl["volume"] == 0.7
    assert ctrl["length_scale"] == 1.18

    set_pipeline_mykyta_controls({"rate": 1.05, "pitch": 1.0, "volume": 1.1, "length_scale": 0.95})
    seg: dict = {}
    stamp_tts_backend_meta(seg, engine_id="tts_uk", voice="mykyta")
    assert seg["tts_backend"] == "tts_uk"
    assert seg["tts_voice"] == "mykyta"
    assert seg["tts_rate"] == 1.05
    assert seg["tts_pitch"] == 1.0
    assert seg["tts_volume"] == 1.1
    assert seg["tts_length_scale"] == 0.95
    set_pipeline_mykyta_controls(None)


def test_stage22_meta_status_dead_air():
    from engines.closed_loop_timing import TimingBudget, _stamp_stage19e_fields

    seg = {
        "text": "Короткий текст.",
        "final_tts_text": "Короткий текст.",
        "tts_backend": "tts_uk",
        "tts_voice": "mykyta",
        "tts_rate": 1.0,
        "tts_pitch": 0.0,
        "tts_volume": 1.0,
        "tts_length_scale": 1.0,
    }
    budget = TimingBudget(
        index=0,
        slot_duration=5000,
        measured_duration=2000,
        original_duration=2000,
        underflow=3000,
        overflow=0,
        final_status="dead_air_risk",
        delta=-3000,
    )
    _stamp_stage19e_fields(
        seg,
        budget=budget,
        algorithm_reason="dead_air_risk",
        expand_executed=False,
        shorten_executed=False,
        split_executed=False,
    )
    meta = seg.get("stage22") or {}
    assert meta.get("final_status") == "dead_air_risk"
    assert "tts_rate" in meta
    assert float(meta.get("fill_ratio") or 0) < 0.90


def test_stage22_in_band_fill_not_dead_air_despite_abs_underflow():
    from engines.closed_loop_timing import TimingBudget, _stamp_stage19e_fields

    seg = {
        "text": "Досить довгий текст щоб заповнити слот майже повністю без сміття.",
        "final_tts_text": "Досить довгий текст щоб заповнити слот майже повністю без сміття.",
        "tts_backend": "tts_uk",
        "tts_voice": "mykyta",
    }
    # fill = 17000/17500 ≈ 0.971 but abs delta = -500 > 350
    budget = TimingBudget(
        index=0,
        slot_duration=17500,
        measured_duration=17000,
        original_duration=17000,
        underflow=500,
        overflow=0,
        final_status="dead_air_risk",
        delta=-500,
    )
    _stamp_stage19e_fields(
        seg,
        budget=budget,
        algorithm_reason="dead_air_risk",
        expand_executed=False,
        shorten_executed=False,
        split_executed=False,
    )
    meta = seg.get("stage22") or {}
    assert 0.90 <= float(meta.get("fill_ratio") or 0) <= 1.12
    assert meta.get("final_status") == "ok"


def test_ripple_shift_segment_dicts():
    from engines.conflict_resolver import ripple_shift_segment_dicts

    segs = [
        {
            "index": 0,
            "start_time_ms": 0,
            "final_tts_duration_ms": 2500,
        },
        {
            "index": 1,
            "start_time_ms": 1800,
            "final_tts_duration_ms": 1000,
        },
    ]
    # overlap 700 > 400
    stats = ripple_shift_segment_dicts(segs)
    assert stats["severe_shifted"] >= 1
    assert segs[1]["start_time_ms"] >= 2500
    assert segs[1].get("merge_adjusted_start") >= 2500


def test_ripple_shift_on_severe_overlap():
    from engines.conflict_resolver import (
        STAGE22_RIPPLE_OVERLAP_MS,
        SegmentPlacement,
        resolve_conflicts,
    )

    from engines.conflict_resolver import STAGE23_RIPPLE_OVERLAP_MS

    assert STAGE22_RIPPLE_OVERLAP_MS == 400
    assert STAGE23_RIPPLE_OVERLAP_MS == 80
    a = SegmentPlacement(
        idx=0,
        original_start_ms=0,
        slot_end_ms=2000,
        place_start_ms=0,
        duration_ms=2500,
    )
    b = SegmentPlacement(
        idx=1,
        original_start_ms=1800,
        slot_end_ms=4000,
        place_start_ms=1800,
        duration_ms=1000,
    )
    # overlap = 2500 - 1800 = 700 > 300
    result = resolve_conflicts([a, b], collect_traces=False)
    segs = {s.idx: s for s in result.segments}
    assert segs[1].place_start_ms >= segs[0].effective_end_ms
    assert segs[1].strategy == "ripple_shift" or segs[0].strategy != "overflow"
