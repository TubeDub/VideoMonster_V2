"""Start and poll auto-dub tasks without modifying the pipeline."""

from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from engines.stress_test.config import poll_interval_sec, task_timeout_sec

logger = logging.getLogger(__name__)


def start_dub_task(
    *,
    video_path: str,
    app_dir: Path,
    target_lang: str = "uk",
    source_lang: str = "en",
    voice: str = "uk-UA-OstapNeural",
    model_size: str = "tiny",
    dub_style: str = "modern",
    translation_review_before_tts: bool = False,
    mix_volume: float = 0.3,
) -> str:
    """Create auto-dub task using existing pipeline entry point."""
    from api.auto_dub_api import (
        AUTO_TASKS,
        AUTO_TASK_CONTROLS,
        STATE_LOCK,
        _run_pipeline,
        _store_style_profile,
    )
    from engines.dub_style_presets import resolve_dub_style

    vp = Path(video_path)
    if not vp.is_file():
        raise FileNotFoundError(video_path)

    resolved_style = resolve_dub_style(dub_style)
    mix_mode = resolved_style["mix_mode"]
    mix_volumes = resolved_style["mix_volumes"]
    skip_tts = bool(resolved_style.get("skip_tts"))
    tts_rate = resolved_style.get("tts_rate")
    tts_pitch = resolved_style.get("tts_pitch")
    style_id = resolved_style["style_id"]

    task_id = uuid.uuid4().hex
    with STATE_LOCK:
        AUTO_TASKS[task_id] = {
            "status": "running",
            "step": "preparing",
            "progress": 0.0,
            "ui_lang": "ru",
            "steps_done": 0,
            "errors": [],
            "output_file": None,
            "info": {
                "segments_data": [],
                "source_segments": [],
                "timing_map_backup": [],
                "timed_audio": None,
                "skip_translate": False,
                "preload": {},
                "dub_style": style_id,
                "skip_tts": skip_tts,
                "translation_review_before_tts": translation_review_before_tts,
                "tts_rate": tts_rate,
                "tts_pitch": tts_pitch,
                "tts_engine": "edge-offline",
                "target_lang": target_lang,
                "source_lang": source_lang,
            },
        }
        _store_style_profile(AUTO_TASKS[task_id]["info"], resolved_style)
        AUTO_TASK_CONTROLS[task_id] = {
            "state": "running",
            "editing": False,
            "editor_error": False,
            "current_segment": 0,
            "stop_after_segment": False,
            "awaiting_translation_review": False,
        }

    threading.Thread(
        target=_run_pipeline,
        kwargs={
            "task_id": task_id,
            "video_path": str(vp.resolve()),
            "target_lang": target_lang,
            "voice": voice,
            "model_size": model_size,
            "mix_mode": mix_mode,
            "mix_volumes": mix_volumes,
            "keep_original_track": False,
            "dub_mode": "replace",
            "mix_volume": mix_volume,
            "source_lang": source_lang,
            "target_duration_ms": None,
            "skip_translate": False,
            "ui_lang": "ru",
            "segmentation_mode": "timing",
            "ocr_enabled": False,
            "dub_style": style_id,
            "skip_tts": skip_tts,
            "tts_rate": tts_rate,
            "tts_pitch": tts_pitch,
        },
        daemon=True,
    ).start()
    return task_id


def poll_task(task_id: str) -> dict[str, Any]:
    from api.auto_dub_api import AUTO_TASKS, AUTO_TASK_CONTROLS, STATE_LOCK

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        control = AUTO_TASK_CONTROLS.get(task_id)
        if not task:
            return {"status": "error", "error": "task_not_found"}
        return {
            "status": task.get("status"),
            "step": task.get("step"),
            "progress": task.get("progress"),
            "errors": list(task.get("errors") or []),
            "output_file": task.get("output_file"),
            "awaiting_translation_review": bool(
                control and control.get("awaiting_translation_review")
            ),
        }


def get_task_snapshot(task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from api.auto_dub_api import AUTO_TASKS, STATE_LOCK

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return {}, {}
        return copy.deepcopy(task), copy.deepcopy(task.get("info") or {})


def approve_translation_review(task_id: str) -> None:
    from api.auto_dub_api import _resume_from_translation_review

    _resume_from_translation_review(task_id)


def wait_for_task(
    task_id: str,
    *,
    on_tick: Callable[[dict[str, Any]], None] | None = None,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    deadline = time.time() + (timeout_sec or task_timeout_sec())
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = poll_task(task_id)
        if on_tick:
            on_tick(last)
        st = last.get("status")
        if st == "translation_review":
            approve_translation_review(task_id)
            time.sleep(poll_interval_sec())
            continue
        if st in ("done", "error"):
            return last
        time.sleep(poll_interval_sec())
    return {"status": "timeout", "errors": ["stress_test_timeout"], **last}


def build_review(info: dict[str, Any]) -> dict[str, Any]:
    from engines.translation_review import build_translation_review

    return build_translation_review(info)


def collect_log_paths(info: dict[str, Any], task_id: str, app_dir: Path) -> list[str]:
    paths: list[str] = []
    dev = info.get("dev_diagnostics") or {}
    if isinstance(dev, dict):
        paths.extend(str(v) for v in dev.values() if v)
    for name in (
        f"translation_{task_id}.log",
        f"tts_{task_id}.log",
        f"timing_{task_id}.log",
    ):
        p = app_dir / "output" / "dev" / name
        if p.is_file():
            paths.append(str(p))
    perf = app_dir / "output" / "dev" / "performance_report.log"
    if perf.is_file():
        paths.append(str(perf))
    return paths
