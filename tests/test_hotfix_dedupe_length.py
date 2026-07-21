"""Hotfix: consecutive dedupe must preserve segment alignment."""

from __future__ import annotations

from engines.translation_naturalizer import dedupe_consecutive_similar


def test_dedupe_preserves_length_on_identical_neighbors():
    lines = [
        "Тож Джордж-молодший вирішив більше не гоняти.",
        "Тож Джордж-молодший вирішив більше не гоняти. Замість цього взяв камеру.",
        "Насправді він подав заявку до USC.",
    ]
    out = dedupe_consecutive_similar(lines, threshold=0.6)
    assert len(out) == len(lines)
    assert out[0]
    # Near-duplicate neighbour cleared, not dropped (keeps index alignment).
    assert out[1] == ""
    assert "USC" in out[2]


def test_dedupe_identical_pair_keeps_two_slots():
    lines = ["same full group translation text"] * 2
    out = dedupe_consecutive_similar(lines)
    assert len(out) == 2
    assert out[0] == "same full group translation text"
    assert out[1] == ""
