"""Tests — sentence-boundary-safe segmentation (TZ §8)."""

from __future__ import annotations

from engines.cleaner import (
    _distribute_text_by_timing_sentences,
    _ends_complete_sentence,
    detect_split_sentences,
    split_by_timing_map,
)


def test_sentence_split_never_breaks_sentence():
    text = "Перше речення. Друге речення. Третє речення."
    timing_map = [{"start": 0, "end": 2000}, {"start": 2100, "end": 4000}, {"start": 4100, "end": 6000}]
    parts = split_by_timing_map(text, timing_map)
    assert len(parts) == 3
    for part in parts:
        if part.strip():
            assert _ends_complete_sentence(part)


def test_more_sentences_than_slots_merges_whole_sentences():
    text = "One. Two. Three. Four."
    timing_map = [{"start": 0, "end": 1000}, {"start": 1100, "end": 2000}]
    parts = _distribute_text_by_timing_sentences(text, timing_map)
    assert len(parts) == 2
    assert "One." in parts[0]
    assert "Two." in parts[0] or "Three." in parts[1]


def test_detect_split_sentences_finds_mid_sentence_break():
    segments = ["Це незавершене речення без", "крапки в кінці"]
    issues = detect_split_sentences(segments)
    assert any(i["code"] == "split_sentence" for i in issues)


def test_single_sentence_multiple_slots_keeps_whole_sentence():
    text = "Одне довге речення з багатьма словами."
    timing_map = [{"start": 0, "end": 1000}, {"start": 1100, "end": 2000}]
    parts = _distribute_text_by_timing_sentences(text, timing_map)
    assert parts[0].startswith("Одне")
    assert parts[0].endswith(".")
