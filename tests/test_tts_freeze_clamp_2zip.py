# -*- coding: utf-8 -*-
"""2.zip RCA — freeze must not invent indices past segments_data."""

from __future__ import annotations

from engines.tts_review_align import freeze_spoken_to_review_final


def test_freeze_clamps_to_segments_data_length():
    """Audits with a higher index must not pad the TTS text list past live rows."""
    segments_data = [
        {"text": "A", "final_tts_text": "A"},
        {"text": "B", "final_tts_text": "B"},
    ]
    audits = [
        {"index": 0, "final_text": "A"},
        {"index": 1, "final_text": "B"},
        {"index": 2, "final_text": "ORPHAN SHOULD NOT APPEAR"},
    ]
    out = freeze_spoken_to_review_final(
        ["A", "B", "orphan-seed"],
        segments_data,
        audits,
        source_segments=["a", "b", "c"],
    )
    assert len(out) == 2
    assert out[0] == "A"
    assert out[1] == "B"
