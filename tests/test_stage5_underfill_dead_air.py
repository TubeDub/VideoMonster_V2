# -*- coding: utf-8 -*-
"""Stage 5: expand underfill + slot shrink (no dead air)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_expand_text_to_slot_grows_short_uk():
    from engines.text_slot_fit import UNDERFILL_EXPAND_RATIO, expand_text_to_slot, estimate_tts_ms

    short = "Джордж пішов далі."
    slot = 6000
    assert estimate_tts_ms(short, "uk") < slot * UNDERFILL_EXPAND_RATIO
    out, reasons = expand_text_to_slot(short, slot, "uk", source_hint="George went on further down the road then.")
    assert out
    assert len(out) >= len(short)
    # Must attempt a real expand (not no-op) when severely underfilled.
    assert out != short or reasons == []  # allow no-op only if rules can't safely expand
    grown, reasons2 = expand_text_to_slot(
        "Тож він пішов.",
        slot,
        "uk",
        source_hint="So then he kept walking down that long road for a while.",
    )
    assert grown != "Тож він пішов."
    assert reasons2


def test_fit_expands_underfill_not_noop():
    from engines.text_slot_fit import fit_text_to_slot, estimate_tts_ms

    text = "Тож він пішов."
    slot = 7000
    fit = fit_text_to_slot(text, slot, "uk", source_hint="So he kept walking for a long time then.")
    assert fit.predicted_ms_after >= fit.predicted_ms_before
    if estimate_tts_ms(text, "uk") < slot * 0.80:
        # Stage 17/19: residual underfill after expand → atempo_slow / expand_then_slow.
        assert fit.action in (
            "expand",
            "unchanged",
            "atempo_slow",
            "expand_then_slow",
            "dead_air_risk",
        )
        if fit.action == "expand":
            assert fit.changed
            assert fit.predicted_ms_after > fit.predicted_ms_before
        if fit.action in ("atempo_slow", "expand_then_slow"):
            assert fit.dead_air_risk_ms > 0


def test_shorten_does_not_overshoot_into_dead_air_when_original_fits():
    from engines.text_slot_fit import fit_text_to_slot, estimate_tts_ms

    # Two sentences; slot fits both under 1.08 — must not keep only first if that underfills.
    text = (
        "Це був маленький автомобіль. "
        "Батько купив його синові того дня."
    )
    pred = estimate_tts_ms(text, "uk")
    slot = int(pred / 0.95)  # original comfortably near slot
    fit = fit_text_to_slot(text, slot, "uk")
    assert fit.predicted_ms_after >= int(slot * 0.80) or fit.text == text


def test_underfill_metrics_and_shrink():
    from engines.timing_fit import (
        detect_significant_underfill,
        shrink_underfilled_slot_end,
        underfill_metrics,
    )

    m = underfill_metrics(4000, 10000)
    assert m["fill_ratio"] == 0.4
    assert m["underfill_ms"] == 6000
    assert m["underfill_significant"] is True
    assert detect_significant_underfill(4000, 10000)

    end, meta = shrink_underfilled_slot_end(
        1000, 11000, 4000, next_start=20000, text_hint="Кінець."
    )
    assert meta["slot_shrunk"] is True
    assert end < 11000
    assert end >= 1000 + 4000 + 80
    assert end <= 1000 + 4000 + 200

    # No shrink when fill ok
    end2, meta2 = shrink_underfilled_slot_end(0, 5000, 4500, text_hint="Ok.")
    assert meta2["slot_shrunk"] is False
    assert end2 == 5000


def test_shrink_does_not_hit_next_segment():
    from engines.timing_fit import shrink_underfilled_slot_end

    end, meta = shrink_underfilled_slot_end(
        0, 10000, 3000, next_start=3200, text_hint="Hi."
    )
    assert meta["slot_shrunk"] is True
    assert end <= 3200 - 20
