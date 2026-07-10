#!/usr/bin/env python3
"""Tests for Word Timing Map Phase 0/1 — persist, merge, checkpoints."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.word_timing_map.extract import proportional_word_split
from engines.word_timing_map.models import SegmentWordMap
from engines.word_timing_map.phase0 import WtmCheckpointLog
from engines.word_timing_map.pipeline import (
    attach_word_maps_to_segments_data,
    build_merged_word_maps,
    build_raw_word_maps,
    persist_task_word_maps,
    sync_timing_map_words,
    word_maps_from_task_info,
)


def test_proportional_estimated():
    words = proportional_word_split("one two three four five", 0, 5000)
    assert len(words) == 5
    assert words[0].start_ms == 0
    assert words[-1].end_ms == 5000
    assert all(w.confidence == 0.5 for w in words)


def test_raw_maps_estimated_without_whisper_words():
    segments = ["Hello world test"]
    timing = [{"start": 100, "end": 3100}]
    maps = build_raw_word_maps(segments, timing)
    assert len(maps) == 1
    assert maps[0].timing_source == "estimated"
    assert len(maps[0].words) == 3


def test_raw_maps_real_with_embedded_words():
    segments = ["George was driving"]
    timing = [
        {
            "start": 0,
            "end": 2000,
            "words": [
                {"text": "George", "start_ms": 0, "end_ms": 400, "confidence": 0.95},
                {"text": "was", "start_ms": 410, "end_ms": 550, "confidence": 0.92},
                {"text": "driving", "start_ms": 560, "end_ms": 1200, "confidence": 0.88},
            ],
        }
    ]
    maps = build_raw_word_maps(segments, timing)
    assert maps[0].timing_source == "real"
    assert len(maps[0].words) == 3


def test_merge_preserves_all_words():
    raw_segs = ["Hello world", "how are you"]
    raw_timing = [{"start": 0, "end": 1000}, {"start": 1100, "end": 2500}]
    merged_segs = ["Hello world how are you"]
    merged_timing = [{"start": 0, "end": 2500}]
    merged = build_merged_word_maps(raw_segs, raw_timing, merged_segs, merged_timing)
    assert len(merged) == 1
    assert len(merged[0].words) == 5
    assert merged[0].words[0].text == "Hello"
    assert merged[0].words[-1].text == "you"


def test_persist_through_task_info():
    segments = ["Test segment here"]
    timing = [{"start": 0, "end": 3000}]
    maps = build_raw_word_maps(segments, timing)
    info: dict = {}
    persist_task_word_maps(info, maps, timing_map=timing)
    assert len(info["source_word_maps"]) == 1
    assert info["word_timing_meta"]["words_total"] == 3
    synced = sync_timing_map_words(timing, maps)
    assert synced[0].get("timing_source") == "estimated"
    assert len(synced[0].get("words") or []) == 3

    segs_data = attach_word_maps_to_segments_data(
        [{"index": 0, "text": segments[0], "file": None}], maps
    )
    assert segs_data[0]["source_word_map"]["timing_source"] == "estimated"
    info["segments_data"] = segs_data
    roundtrip = word_maps_from_task_info(info)
    assert len(roundtrip[0].words) == 3


def test_phase0_checkpoints_stable():
    info: dict = {}
    segments = ["Hello world test"]
    timing = [{"start": 0, "end": 3000}]
    maps = build_raw_word_maps(segments, timing)
    persist_task_word_maps(info, maps, timing_map=timing)
    log = WtmCheckpointLog("test", ROOT)
    cp1 = log.record(info, "post_merge")
    assert cp1.ok
    cp2 = log.record(info, "post_translate")
    assert cp2.ok
    cp3 = log.record(info, "post_sso")
    assert cp3.ok
    assert log.all_ok()


def main():
    test_proportional_estimated()
    test_raw_maps_estimated_without_whisper_words()
    test_raw_maps_real_with_embedded_words()
    test_merge_preserves_all_words()
    test_persist_through_task_info()
    test_phase0_checkpoints_stable()
    print("OK: test_word_timing_map")


if __name__ == "__main__":
    main()
