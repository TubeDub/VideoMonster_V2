"""Tests for shared auto-dub task registry lifecycle."""

from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_task_state():
    from engines import dub_task_state as dts

    with dts.STATE_LOCK:
        dts.AUTO_TASKS.clear()
        dts.AUTO_TASK_CONTROLS.clear()
    yield
    with dts.STATE_LOCK:
        dts.AUTO_TASKS.clear()
        dts.AUTO_TASK_CONTROLS.clear()


def test_init_auto_task_sets_timestamps():
    from engines.dub_task_state import AUTO_TASKS, init_auto_task

    init_auto_task("abc", {"status": "running", "info": {}})
    task = AUTO_TASKS["abc"]
    assert task["_created_at"] > 0
    assert task["_last_touch"] > 0


def test_evict_terminal_task_after_ttl(tmp_path, monkeypatch):
    from engines import dub_task_state as dts

    monkeypatch.setattr(dts, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(dts, "AUTO_TASK_TERMINAL_TTL_SEC", 1)

    dts.init_auto_task(
        "done1",
        {"status": "done", "info": {}, "output_file": "final.mp4"},
    )
    with dts.STATE_LOCK:
        dts.AUTO_TASKS["done1"]["_last_touch"] = time.time() - 10

    removed = dts.evict_expired_auto_tasks()
    assert removed == 1
    assert "done1" not in dts.AUTO_TASKS


def test_running_task_not_evicted():
    from engines import dub_task_state as dts

    dts.init_auto_task("run1", {"status": "running", "info": {}})
    with dts.STATE_LOCK:
        dts.AUTO_TASKS["run1"]["_last_touch"] = time.time() - 99999

    removed = dts.evict_expired_auto_tasks()
    assert removed == 0
    assert "run1" in dts.AUTO_TASKS


def test_cleanup_respects_keep_studio_assets(tmp_path, monkeypatch):
    from engines import dub_task_state as dts

    monkeypatch.setattr(dts, "OUTPUT_DIR", tmp_path)
    seg_file = tmp_path / "abc_seg0001.mp3"
    seg_file.write_bytes(b"mp3")

    task = {
        "status": "studio_ready",
        "info": {
            "keep_studio_assets": True,
            "segments_data": [{"file": seg_file.name}],
            "tts_files": [seg_file.name],
        },
    }
    removed = dts.cleanup_task_tts_files("abc", task, output_dir=tmp_path)
    assert removed == 0
    assert seg_file.exists()


def test_cleanup_removes_tts_when_assets_not_kept(tmp_path, monkeypatch):
    from engines import dub_task_state as dts

    monkeypatch.setattr(dts, "OUTPUT_DIR", tmp_path)
    seg_file = tmp_path / "abc_seg0001.mp3"
    seg_file.write_bytes(b"mp3")
    out_file = tmp_path / "video_OUTPUT_abc.mp4"
    out_file.write_bytes(b"mp4")

    task = {
        "status": "done",
        "output_file": out_file.name,
        "info": {
            "keep_studio_assets": False,
            "segments_data": [{"file": seg_file.name}],
            "tts_files": [seg_file.name],
            "mux_base_id": "abc",
        },
    }
    removed = dts.cleanup_task_tts_files("abc", task, output_dir=tmp_path)
    assert removed >= 1
    assert not seg_file.exists()
    assert out_file.exists()


def test_touch_task_extends_activity():
    from engines.dub_task_state import AUTO_TASKS, init_auto_task, touch_task

    init_auto_task("t1", {"status": "studio_ready", "info": {}})
    old = AUTO_TASKS["t1"]["_last_touch"]
    time.sleep(0.01)
    touch_task("t1")
    assert AUTO_TASKS["t1"]["_last_touch"] >= old
