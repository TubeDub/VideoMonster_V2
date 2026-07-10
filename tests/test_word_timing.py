"""Tests for engines.word_timing facade."""

from __future__ import annotations

from engines.word_timing import build_from_whisper, WordTimingMap


def test_build_from_whisper_proportional():
    segments = ["one two three"]
    timing = [{"start": 0, "end": 3000}]
    maps = build_from_whisper(segments, timing)
    assert len(maps) == 1
    assert isinstance(maps[0], WordTimingMap)
    assert len(maps[0].words) == 3
    assert maps[0].words[0].start_ms == 0
    assert maps[0].words[-1].end_ms == 3000


def test_build_from_whisper_embedded_words():
    segments = ["Hello world"]
    timing = [
        {
            "start": 0,
            "end": 2000,
            "words": [
                {"text": "Hello", "start_ms": 0, "end_ms": 800, "confidence": 0.9},
                {"text": "world", "start_ms": 850, "end_ms": 1800, "confidence": 0.88},
            ],
        }
    ]
    maps = build_from_whisper(segments, timing)
    assert maps[0].timing_source == "real"
    assert len(maps[0].words) == 2
