"""P2 AudioTimingOptimizer + deterministic dub tests."""

from __future__ import annotations

import copy

from engines.audio_timing_optimizer import (
    AudioTimingOptimizer,
    deterministic_fingerprint,
    optimize_audio_timing,
)
from engines.pipeline_integrity.translation_lock import lock_segments


def _seg(i: int, **kwargs):
    base = {
        "segment_id": f"seg-{i:03d}",
        "index": i,
        "translated_text": f"Текст {i}",
        "text": f"Текст {i}",
        "start_ms": i * 1000,
        "end_ms": i * 1000 + 400,
        "playback_duration": 800,  # overflows 400ms slot
        "translation_locked": True,
    }
    base.update(kwargs)
    return base


class TestAudioTimingOptimizer:
    def test_does_not_change_locked_text(self):
        rows = [_seg(0), _seg(1, start_ms=1000, end_ms=1400)]
        info = {"pipeline_state": "LOCKED", "translation_locked": True, "segments_data": rows}
        before = copy.deepcopy(rows[0]["translated_text"])
        result = optimize_audio_timing(rows, info=info)
        assert rows[0]["translated_text"] == before
        assert result.metrics.overflow_count >= 0
        assert "audio_timing_optimizer" in info

    def test_resolves_overlap_via_scheduler(self):
        rows = [
            _seg(0, start_ms=0, end_ms=1200, playback_duration=200),
            _seg(1, start_ms=1000, end_ms=1500, playback_duration=200),
        ]
        optimize_audio_timing(rows)
        assert int(rows[1]["start_ms"]) >= int(rows[0]["end_ms"])

    def test_deterministic_fingerprint(self):
        rows_a = [_seg(0), _seg(1)]
        rows_b = copy.deepcopy(rows_a)
        settings = {"tempo_max": 1.1}
        opt = AudioTimingOptimizer()
        r1 = opt.optimize_project(rows_a, settings=settings)
        r2 = opt.optimize_project(rows_b, settings=settings)
        assert r1.fingerprint == r2.fingerprint
        assert deterministic_fingerprint(rows_a, settings=settings) == r1.fingerprint

    def test_overflow_marked_without_text_change(self):
        rows = [_seg(0, start_ms=0, end_ms=200, playback_duration=2000)]
        info = {"pipeline_state": "VALIDATED", "segments_data": rows}
        lock_segments(rows, info=info)
        text = rows[0]["translated_text"]
        result = optimize_audio_timing(rows, info=info)
        assert rows[0]["translated_text"] == text
        assert rows[0].get("overflow") is True or result.overflow
