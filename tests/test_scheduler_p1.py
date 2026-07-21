"""Unit tests — Scheduler API (Freeze TZ P1)."""

from __future__ import annotations

import pytest

from engines.pipeline_integrity import PipelineState, get_pipeline_state, lock_segments
from engines.scheduler import Scheduler, SchedulerError, request_time, update_time
from engines.scheduler.api import SCHEDULER_TIMING_FIELDS


def _seg(**kwargs):
    base = {
        "segment_id": "seg-a",
        "index": 0,
        "translated_text": "Привіт",
        "text": "Привіт",
        "start_ms": 0,
        "end_ms": 1000,
    }
    base.update(kwargs)
    return base


class TestSchedulerUpdateTime:
    def test_update_time_sets_start_end(self):
        rows = [_seg()]
        result = update_time(rows, "seg-a", start_ms=100, end_ms=1500)
        assert rows[0]["start_ms"] == 100
        assert rows[0]["end_ms"] == 1500
        assert "start_ms" in result["updated"]

    def test_update_time_rejects_unknown_segment(self):
        with pytest.raises(SchedulerError):
            update_time([_seg()], "missing", start_ms=1)

    def test_update_time_rejects_inverted_range(self):
        with pytest.raises(SchedulerError):
            update_time([_seg()], "seg-a", start_ms=500, end_ms=100)

    def test_request_time_extends_slot(self):
        rows = [_seg(start_ms=0, end_ms=500)]
        result = request_time(rows, "seg-a", 1200)
        assert rows[0]["end_ms"] - rows[0]["start_ms"] == 1200
        assert result["required_ms"] == 1200

    def test_scheduler_does_not_touch_text(self):
        rows = [_seg(translation_locked=True, translated_text="LOCK")]
        update_time(rows, "seg-a", start_ms=10, end_ms=900)
        assert rows[0]["translated_text"] == "LOCK"
        assert "translated_text" not in SCHEDULER_TIMING_FIELDS

    def test_advances_pipeline_to_scheduled(self):
        info = {"pipeline_state": "TTS_READY", "segments_data": [_seg()]}
        sched = Scheduler(info=info)
        sched.update_time(info["segments_data"], "seg-a", start_ms=0, end_ms=800)
        assert get_pipeline_state(info) == PipelineState.SCHEDULED

    def test_works_after_translation_lock(self):
        info = {"pipeline_state": "VALIDATED", "segments_data": [_seg()]}
        lock_segments(info["segments_data"], info=info)
        update_time(
            info["segments_data"],
            "seg-a",
            start_ms=20,
            end_ms=1020,
            info=info,
        )
        assert info["segments_data"][0]["start_ms"] == 20
        assert info["segments_data"][0]["translated_text"] == "Привіт"
