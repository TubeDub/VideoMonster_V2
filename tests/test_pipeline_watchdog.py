"""Tests for pipeline watchdog and stall detection."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def task_env():
    from engines.dub_task_state import (
        AUTO_TASK_CONTROLS,
        AUTO_TASKS,
        CANCEL_FLAGS,
        STATE_LOCK,
        init_auto_task,
        register_pipeline_thread,
    )

    task_id = "watchdog-test-1"
    with STATE_LOCK:
        AUTO_TASKS.pop(task_id, None)
        AUTO_TASK_CONTROLS.pop(task_id, None)
        CANCEL_FLAGS.pop(task_id, None)
    init_auto_task(
        task_id,
        {
            "status": "running",
            "step": "translate",
            "progress": 55,
            "steps_done": 3,
            "errors": [],
            "info": {"ui_lang": "ru", "progress_detail": {}},
            "ui_lang": "ru",
        },
    )
    with STATE_LOCK:
        AUTO_TASK_CONTROLS[task_id] = {"state": "running", "current_segment": 0}
    alive = threading.Event()

    def _worker():
        alive.wait(30)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    register_pipeline_thread(task_id, t)
    yield task_id, t, alive
    alive.set()
    with STATE_LOCK:
        AUTO_TASKS.pop(task_id, None)
        AUTO_TASK_CONTROLS.pop(task_id, None)
        CANCEL_FLAGS.pop(task_id, None)


def test_watchdog_heartbeat_updates_idle(task_env):
    from engines.pipeline_watchdog import PipelineWatchdog

    task_id, _, _ = task_env
    wd = PipelineWatchdog(task_id)
    wd.stage_start("translate")
    wd.heartbeat(segments_done=1, total_segments=10, current_segment=1)
    snap = wd.snapshot()
    assert snap["segments_done"] == 1
    assert snap["stage"] == "translate"


def test_stall_on_dead_thread(task_env):
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    from engines.pipeline_watchdog import PipelineWatchdog, stall_pipeline_task

    task_id, thread, alive = task_env
    alive.set()  # stop worker
    thread.join(timeout=2.0)

    wd = PipelineWatchdog(task_id, on_stall=stall_pipeline_task)
    wd.stage_start("translate")
    wd._ticks = 3
    wd._stage.thread_alive = False

    with patch.object(wd, "_handle_stall") as mock_stall:
        wd._tick()
        assert mock_stall.called


def test_cancel_runtime_flags():
    from engines.dub_task_state import (
        cancel_pipeline_runtime,
        is_cancel_requested,
        request_cancel,
    )

    tid = "cancel-test"
    request_cancel(tid, reason="test")
    assert is_cancel_requested(tid)
    result = cancel_pipeline_runtime(tid, join_timeout=0.1)
    assert "thread_joined" in result


def test_stall_pipeline_sets_status(task_env, tmp_path):
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    from engines.pipeline_watchdog import stall_pipeline_task

    task_id, _, alive = task_env
    alive.set()
    report = {
        "message": "Этап завис: Перевод",
        "reason_code": "PIPELINE_STALLED",
        "step": "translate",
        "stage_label": "Перевод",
        "idle_sec": 120,
        "probable_cause": "llm_slow",
    }
    stall_pipeline_task(task_id, report)
    with STATE_LOCK:
        assert AUTO_TASKS[task_id]["status"] == "stalled"
        assert AUTO_TASKS[task_id]["info"].get("pipeline_stall")


def test_preparing_with_heartbeat_not_stalled(task_env):
    """CPU init / Whisper must not false-stall when heartbeats arrive."""
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    from engines.pipeline_watchdog import PipelineWatchdog

    task_id, _, _ = task_env
    with STATE_LOCK:
        AUTO_TASKS[task_id]["step"] = "preparing"
        AUTO_TASKS[task_id]["info"]["progress_detail"] = {}

    wd = PipelineWatchdog(task_id)
    wd.stage_start("preparing")
    wd._ticks = 5
    wd._stage.started_at = time.time() - 100.0
    wd._stage.last_progress_at = time.time() - 100.0

    wd.heartbeat(phase="preparing", live_message="Инициализация…")
    wd._tick()
    assert not wd._stall_reported


def test_step_change_resets_idle_timer(task_env):
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    from engines.pipeline_watchdog import PipelineWatchdog

    task_id, _, _ = task_env
    wd = PipelineWatchdog(task_id)
    wd.stage_start("preparing")
    wd._stage.last_progress_at = time.time() - 200.0

    with STATE_LOCK:
        AUTO_TASKS[task_id]["step"] = "extract_audio"
        AUTO_TASKS[task_id]["progress"] = 2.0

    wd._tick()
    assert wd._stage.stage == "extract_audio"
    assert wd._stage.idle_sec() < 5.0
    assert not wd._stall_reported


def test_voice_verification_phase_uses_longer_threshold(task_env):
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    from engines.pipeline_watchdog import PipelineWatchdog, STALL_IDLE_SEC

    task_id, _, _ = task_env
    with STATE_LOCK:
        AUTO_TASKS[task_id]["step"] = "tts"
        AUTO_TASKS[task_id]["info"]["progress_detail"] = {
            "phase": "voice_verification",
            "tts_substep": "voice_verify",
            "last_heartbeat_at": time.time(),
        }

    wd = PipelineWatchdog(task_id)
    wd.stage_start("tts")
    wd._ticks = 5
    wd._stage.last_progress_at = time.time() - 120.0

    wd._tick()
    assert not wd._stall_reported
    assert STALL_IDLE_SEC["voice_verification"] > STALL_IDLE_SEC["tts"]


def test_voice_verification_heartbeat_prevents_tts_stall(task_env):
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    from engines.pipeline_watchdog import PipelineWatchdog

    task_id, _, _ = task_env
    with STATE_LOCK:
        AUTO_TASKS[task_id]["step"] = "tts"
        AUTO_TASKS[task_id]["info"]["progress_detail"] = {
            "phase": "voice_verification",
            "tts_substep": "voice_verify",
            "current_segment": 3,
            "total_segments": 20,
            "verification_attempt": 2,
            "last_heartbeat_at": time.time(),
        }

    wd = PipelineWatchdog(task_id)
    wd.stage_start("tts")
    wd._ticks = 5
    wd._stage.last_progress_at = time.time() - 100.0

    wd.heartbeat(
        phase="voice_verification",
        tts_substep="voice_verify",
        current_segment=3,
        total_segments=20,
        verification_attempt=2,
        verification_route="semantic",
    )
    wd._tick()
    assert not wd._stall_reported
    assert wd._stage.stage == "voice_verification"
