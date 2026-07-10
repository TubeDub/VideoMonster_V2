"""Tests for AI Core pipeline heartbeat helpers."""

from __future__ import annotations

from unittest.mock import patch

from engines.ai_core.pipeline_heartbeat import emit_ai_core_heartbeat, orchestrator_timeout_scale


def test_emit_ai_core_heartbeat_updates_progress_detail():
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

    tid = "hb-test-task"
    with STATE_LOCK:
        AUTO_TASKS[tid] = {"info": {}, "status": "running"}

    emit_ai_core_heartbeat(tid, agent="semantic", live_message="test")
    with STATE_LOCK:
        detail = AUTO_TASKS[tid]["info"]["progress_detail"]
    assert detail.get("phase") == "ai_core"
    assert detail.get("agent") == "semantic"
    assert float(detail.get("last_heartbeat_at") or 0) > 0

    with STATE_LOCK:
        AUTO_TASKS.pop(tid, None)


def test_orchestrator_timeout_scale_cpu():
    with patch("engines.translation_adapt._is_cpu_only", return_value=True):
        assert orchestrator_timeout_scale() == 2.5
    with patch("engines.translation_adapt._is_cpu_only", return_value=False):
        assert orchestrator_timeout_scale() == 1.0
