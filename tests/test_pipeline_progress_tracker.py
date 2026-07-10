"""Tests for pipeline progress transparency."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def task_id():
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, init_auto_task

    tid = "progress-test-1"
    with STATE_LOCK:
        AUTO_TASKS.pop(tid, None)
    init_auto_task(
        tid,
        {
            "status": "running",
            "step": "tts",
            "progress": 70,
            "steps_done": 4,
            "errors": [],
            "ui_lang": "ru",
            "info": {},
        },
    )
    yield tid
    with STATE_LOCK:
        AUTO_TASKS.pop(tid, None)


def test_enrich_progress_fields_tts(task_id):
    from engines.pipeline_progress_tracker import enrich_progress_fields

    out = enrich_progress_fields(
        task_id,
        phase="tts",
        current_segment=7,
        total_segments=20,
        segments_done=6,
        voice="uk-UA-PolinaNeural",
        tts_engine="edge-offline",
        char_count=142,
        llm_model="llama3.1:8b",
    )
    assert out["segments_remaining"] == 14
    assert out["live_message"]
    assert out.get("stage_progress_pct") == 30.0


def test_slow_segment_notice(task_id):
    from engines.pipeline_progress_tracker import (
        _get_state,
        _save_state,
        enrich_progress_fields,
        record_segment_start,
    )

    record_segment_start(task_id, "tts", 7, total_segments=20, char_count=500)
    st = _get_state(task_id)
    st["segment_started_at"] = time.time() - 60
    _save_state(task_id, st)
    out = enrich_progress_fields(
        task_id,
        phase="tts",
        current_segment=7,
        total_segments=20,
        segments_done=6,
    )
    msg = out.get("slow_segment_notice") or out.get("live_message") or ""
    assert "дольше" in msg or "longer" in msg.lower()


def test_save_performance_diagnostics(task_id, tmp_path):
    from engines.pipeline_progress_tracker import (
        record_segment_end,
        record_segment_start,
        save_performance_diagnostics,
    )

    record_segment_start(task_id, "translate", 1, total_segments=5)
    record_segment_end(task_id, "translate", 1)
    path = save_performance_diagnostics(task_id, app_dir=tmp_path)
    assert path and path.is_file()
