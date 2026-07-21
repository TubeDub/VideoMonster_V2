"""Regression: adaptive CONJ_SPLIT must not mint And at / that point stubs."""

from __future__ import annotations

from engines.adaptive_segmentation.core import _safe_split_chunks


def test_and_at_that_point_not_split():
    text = (
        "And at that point his father actually bought him a small Italian car "
        "called the Fiat, but his father did not get the obsession."
    )
    parts = _safe_split_chunks(text)
    assert not any(p.strip().lower() == "and at" for p in parts)
    assert any("and at that point" in p.lower() for p in parts)


def test_so_two_weeks_when_not_split_to_stub():
    text = (
        "So two weeks earlier when George was making that turn and then something "
        "happened, another car smashed into George's car."
    )
    parts = _safe_split_chunks(text)
    assert not any(p.strip().lower() == "so two weeks earlier" for p in parts)
