# -*- coding: utf-8 -*-
"""Stage 2 Happy Path: STT glue ≥4.5–6s, pause <0.8s, batch translate groups."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.segment_merger import (
    HAPPY_PATH_MAX_GAP_MS,
    HAPPY_PATH_MIN_SAFE_MS,
    merge_stt_segments_happy_path,
)
from engines.translation_naturalizer import (
    HAPPY_PATH_MAX_BATCH_SEGMENTS,
    merge_segments_for_translation,
)


def _tm(pairs):
    return [{"start": a, "end": b} for a, b in pairs]


def test_happy_path_constants():
    assert HAPPY_PATH_MIN_SAFE_MS >= 4500
    assert HAPPY_PATH_MIN_SAFE_MS <= 6000
    assert HAPPY_PATH_MAX_GAP_MS == 800


def test_merge_micro_whisper_into_five_second_blocks():
    # 8×1s micro-segments with 200ms gaps → should collapse toward ≥5s blocks
    texts = [f"Word{i}." for i in range(8)]
    # sentence ends on each — fill_to_min must still glue under 5s
    timing = []
    t = 0
    for _ in range(8):
        timing.append({"start": t, "end": t + 1000})
        t += 1200  # 200ms gap
    merged, mt = merge_stt_segments_happy_path(texts, timing)
    assert len(merged) < len(texts)
    # Every block except possibly the last should be near min safe
    for row in mt[:-1]:
        assert (row["end"] - row["start"]) >= 4500


def test_merge_stops_on_long_pause():
    texts = ["Hello there.", "More words here.", "After silence."]
    timing = _tm([(0, 2000), (2200, 4500), (6000, 8000)])  # 1.5s gap before last
    merged, mt = merge_stt_segments_happy_path(texts, timing)
    # First two can glue (gap 200ms); third separated by 1500ms > 800ms
    assert len(merged) >= 2
    assert merged[-1] == "After silence."


def test_merge_respects_speaker_change():
    texts = ["A one.", "A two.", "B one."]
    timing = _tm([(0, 1500), (1600, 3000), (3100, 5000)])
    merged, _ = merge_stt_segments_happy_path(
        texts, timing, speaker_ids=["a", "a", "b"]
    )
    assert any("B one" in m for m in merged)
    # Speaker B must not be glued into A's block
    assert not any("A one" in m and "B one" in m for m in merged)


def test_batch_translate_groups_happy_path_size():
    texts = [f"Seg {i} continues" for i in range(10)]
    timing = _tm([(i * 900, i * 900 + 800) for i in range(10)])
    groups = merge_segments_for_translation(
        texts,
        timing,
        max_gap_ms=800,
        max_batch=HAPPY_PATH_MAX_BATCH_SEGMENTS,
    )
    assert len(groups) < len(texts)
    assert max(len(g) for g in groups) <= HAPPY_PATH_MAX_BATCH_SEGMENTS
