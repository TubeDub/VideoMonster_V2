"""Shared auto-dub task registry and lifecycle management.

Centralises AUTO_TASKS / AUTO_TASK_CONTROLS to break api.auto_dub_api ↔ api.studio_api
circular imports and provide TTL eviction + TTS artifact cleanup.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from threading import RLock

logger = logging.getLogger("tubedub.dub_task_state")

STATE_LOCK = RLock()
AUTO_TASKS: dict[str, dict] = {}
AUTO_TASK_CONTROLS: dict[str, dict] = {}

# Runtime handles for cancel / watchdog (TZ: no hanging threads).
PIPELINE_THREADS: dict[str, threading.Thread] = {}
PIPELINE_PROCS: dict[str, list[subprocess.Popen]] = {}
CANCEL_FLAGS: dict[str, threading.Event] = {}

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))

# studio_ready: user may open Studio and mix later
AUTO_TASK_ACTIVE_TTL_SEC = 6 * 3600
# done / error: shorter retention
AUTO_TASK_TERMINAL_TTL_SEC = 2 * 3600
MAX_AUTO_TASKS = 100


def touch_task(task_id: str) -> None:
    """Refresh last-access timestamp (extends TTL while user is active)."""
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if task is not None:
            task["_last_touch"] = time.time()


def _task_is_protected(task_id: str, task: dict) -> bool:
    if task.get("status") in ("running", "stalled"):
        return True
    control = AUTO_TASK_CONTROLS.get(task_id)
    if control and control.get("editing"):
        return True
    return False


def register_pipeline_thread(task_id: str, thread: threading.Thread) -> None:
    with STATE_LOCK:
        PIPELINE_THREADS[str(task_id)] = thread
        CANCEL_FLAGS.setdefault(str(task_id), threading.Event())


def get_pipeline_thread(task_id: str) -> threading.Thread | None:
    with STATE_LOCK:
        return PIPELINE_THREADS.get(str(task_id))


def register_subprocess(task_id: str, proc: subprocess.Popen) -> None:
    with STATE_LOCK:
        PIPELINE_PROCS.setdefault(str(task_id), []).append(proc)


def is_cancel_requested(task_id: str) -> bool:
    ev = CANCEL_FLAGS.get(str(task_id))
    return bool(ev and ev.is_set())


def request_cancel(task_id: str, *, reason: str = "user") -> None:
    with STATE_LOCK:
        ev = CANCEL_FLAGS.setdefault(str(task_id), threading.Event())
        ev.set()
        control = AUTO_TASK_CONTROLS.get(task_id)
        if control:
            control["cancel_reason"] = reason


def cancel_pipeline_runtime(task_id: str, *, join_timeout: float = 5.0) -> dict:
    """Terminate subprocesses and signal pipeline cancel (best-effort)."""
    tid = str(task_id)
    request_cancel(tid, reason="cancel")
    terminated: list[int] = []
    with STATE_LOCK:
        procs = list(PIPELINE_PROCS.pop(tid, []))
        thread = PIPELINE_THREADS.get(tid)

    for proc in procs:
        try:
            if proc.poll() is None:
                proc.terminate()
                terminated.append(proc.pid or 0)
        except Exception as exc:
            logger.debug("terminate proc failed: %s", exc)

    time.sleep(0.3)
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    joined = False
    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=max(0.1, float(join_timeout)))
        joined = not thread.is_alive()

    with STATE_LOCK:
        PIPELINE_THREADS.pop(tid, None)

    try:
        from engines.pipeline_watchdog import stop_pipeline_watchdog

        stop_pipeline_watchdog(tid)
    except Exception:
        pass

    return {"terminated_pids": terminated, "thread_joined": joined}


def _task_ttl_sec(task: dict) -> int:
    status = str(task.get("status") or "")
    if status in ("done", "error", "stalled"):
        return AUTO_TASK_TERMINAL_TTL_SEC
    if status == "studio_ready":
        return AUTO_TASK_ACTIVE_TTL_SEC
    return AUTO_TASK_ACTIVE_TTL_SEC


def _task_last_touch(task: dict) -> float:
    return float(task.get("_last_touch") or task.get("_created_at") or 0.0)


def cleanup_task_tts_files(
    task_id: str,
    task: dict | None = None,
    *,
    keep_assets: bool | None = None,
    output_dir: Path | None = None,
) -> int:
    """Remove intermediate TTS / timing artifacts for one task."""
    out = output_dir or OUTPUT_DIR
    if task is None:
        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
    if not task:
        return 0

    info = task.get("info") or {}
    if keep_assets is None:
        keep_assets = bool(info.get("keep_studio_assets"))
    if keep_assets:
        return 0

    keep_names: set[str] = set()
    output_file = task.get("output_file")
    if output_file:
        keep_names.add(Path(str(output_file)).name)
    for key in ("output_path_full", "video_path_backup", "subtitle_file"):
        val = info.get(key)
        if val:
            keep_names.add(Path(str(val)).name)

    removed = 0

    def _unlink(path: Path) -> None:
        nonlocal removed
        if not path.is_file():
            return
        if path.name in keep_names:
            return
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.debug("cleanup_task_tts_files skip %s: %s", path, exc)

    for seg in info.get("segments_data") or []:
        f = seg.get("file")
        if f:
            _unlink(out / Path(str(f)).name)

    for name in info.get("tts_files") or []:
        if name:
            _unlink(out / Path(str(name)).name)

    for key in ("timed_audio", "original_audio_path", "extracted_audio_path"):
        val = info.get(key)
        if val:
            _unlink(Path(str(val)))

    session_dir = info.get("session_dir")
    if session_dir and not keep_assets:
        try:
            import shutil

            shutil.rmtree(str(session_dir), ignore_errors=True)
        except OSError as exc:
            logger.debug("cleanup session_dir skip %s: %s", session_dir, exc)
        try:
            from engines.dubbing_engine.project_session import cleanup_session

            cleanup_session(task_id, keep_output=False)
        except Exception:
            pass

    base_id = str(info.get("mux_base_id") or task_id[:8])
    for pattern in (
        f"{base_id}_seg*.mp3",
        f"{base_id}_extracted.mp3",
        f"{base_id}_timed.mp3",
        f"{task_id[:8]}_seg*.mp3",
    ):
        for path in out.glob(pattern):
            _unlink(path)

    if removed:
        logger.info(
            "cleanup_task_tts_files task=%s removed %d files (keep=%s)",
            task_id,
            removed,
            sorted(keep_names),
        )
    return removed


def evict_expired_auto_tasks(*, force: bool = False) -> int:
    """Drop stale tasks from memory and clean their temp artifacts."""
    removed = 0
    with STATE_LOCK:
        now = time.time()
        stale_ids: list[str] = []

        for tid, task in list(AUTO_TASKS.items()):
            if _task_is_protected(tid, task):
                continue
            status = str(task.get("status") or "")
            if status not in ("done", "error", "studio_ready", "stalled", "cancelled"):
                continue
            last_touch = _task_last_touch(task)
            if not last_touch:
                continue
            age = now - last_touch
            if not force and age <= _task_ttl_sec(task):
                continue
            stale_ids.append(tid)

        if len(AUTO_TASKS) > MAX_AUTO_TASKS:
            candidates = [
                (tid, task)
                for tid, task in AUTO_TASKS.items()
                if not _task_is_protected(tid, task)
            ]
            candidates.sort(key=lambda pair: _task_last_touch(pair[1]))
            overflow = len(AUTO_TASKS) - MAX_AUTO_TASKS
            for tid, _task in candidates:
                if tid in stale_ids:
                    continue
                if overflow <= 0:
                    break
                stale_ids.append(tid)
                overflow -= 1

        seen: set[str] = set()
        for tid in stale_ids:
            if tid in seen:
                continue
            seen.add(tid)
            task = AUTO_TASKS.pop(tid, None)
            AUTO_TASK_CONTROLS.pop(tid, None)
            PIPELINE_THREADS.pop(tid, None)
            PIPELINE_PROCS.pop(tid, None)
            CANCEL_FLAGS.pop(tid, None)
            if task is not None:
                cleanup_task_tts_files(tid, task, keep_assets=False, output_dir=OUTPUT_DIR)
            removed += 1

    if removed:
        logger.info("evict_expired_auto_tasks: removed %d task(s)", removed)
    return removed


def init_auto_task(task_id: str, payload: dict) -> None:
    """Register a new task and evict stale entries."""
    now = time.time()
    payload["_created_at"] = now
    payload["_last_touch"] = now
    with STATE_LOCK:
        AUTO_TASKS[task_id] = payload
    evict_expired_auto_tasks()
