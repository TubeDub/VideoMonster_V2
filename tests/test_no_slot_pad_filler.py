# -*- coding: utf-8 -*-
"""No invented slot-fill pacing in Review Final (George Jr. RCA)."""

from __future__ import annotations

from engines.text_slot_fit import expand_text_to_slot, strip_slot_pad_fillers


def test_strip_slot_pad_fillers():
    dirty = (
        "Через два тижні Джордж лежав у лікарні. "
        "Саме так: через два тижні джордж лежав, "
        "ось як це було тоді, ось як це було тоді — ось як це було тоді."
    )
    clean = strip_slot_pad_fillers(dirty)
    assert "ось як це було тоді" not in clean.lower()
    assert "Саме так:" not in clean
    assert "лікарні" in clean


def test_expand_does_not_invent_pacing_filler():
    short = "Тож він пішов."
    out, reasons = expand_text_to_slot(
        short,
        7000,
        "uk",
        source_hint="So then he kept walking down that long road for a while afterward.",
    )
    assert "ось як це було тоді" not in out.lower()
    assert "Саме так:" not in out
    # Mild intensifier expand is OK; invented narrative pad is not.
    if out != short:
        assert reasons
