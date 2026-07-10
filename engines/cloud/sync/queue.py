"""Background sync queue — non-blocking uploads/downloads."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from engines.cloud.models import SyncState


@dataclass
class SyncTask:
    task_id: str
    kind: str  # upload | download | sync_project | backup
    project_id: str = ""
    provider_id: str = "local"
    local_path: str = ""
    remote_path: str = ""
    state: str = SyncState.QUEUED.value
    progress: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    speed_bps: float = 0.0
    error: str = ""
    created_ms: int = 0
    started_ms: int = 0
    finished_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "local_path": self.local_path,
            "remote_path": self.remote_path,
            "state": self.state,
            "progress": round(self.progress, 4),
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "speed_bps": round(self.speed_bps, 2),
            "error": self.error,
            "created_ms": self.created_ms,
            "started_ms": self.started_ms,
            "finished_ms": self.finished_ms,
            "meta": self.meta,
        }


TaskRunner = Callable[[SyncTask, Callable[[SyncTask], None]], None]


class BackgroundSyncQueue:
    def __init__(self, app_dir: Path, *, max_workers: int = 3):
        self.app_dir = Path(app_dir)
        self.max_workers = max(1, max_workers)
        self._tasks: dict[str, SyncTask] = {}
        self._queue: list[str] = []
        self._lock = threading.RLock()
        self._workers: list[threading.Thread] = []
        self._running = False
        self._runners: dict[str, TaskRunner] = {}

    def register_runner(self, kind: str, fn: TaskRunner) -> None:
        self._runners[kind] = fn

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            for i in range(self.max_workers):
                t = threading.Thread(target=self._worker_loop, name=f"cloud-sync-{i}", daemon=True)
                t.start()
                self._workers.append(t)

    def enqueue(self, kind: str, **fields: Any) -> SyncTask:
        task = SyncTask(
            task_id=str(uuid.uuid4()),
            kind=kind,
            created_ms=int(time.time() * 1000),
            **{k: v for k, v in fields.items() if k in SyncTask.__dataclass_fields__},
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._queue.append(task.task_id)
        self.start()
        return task

    def get(self, task_id: str) -> SyncTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(self._tasks.values(), key=lambda t: t.created_ms, reverse=True)
            return [t.to_dict() for t in rows[:limit]]

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.state in (SyncState.SYNCED.value, SyncState.ERROR.value):
                return False
            task.state = SyncState.PAUSED.value
            if task_id in self._queue:
                self._queue = [x for x in self._queue if x != task_id]
            return True

    def _worker_loop(self) -> None:
        while True:
            task_id = None
            with self._lock:
                if self._queue:
                    task_id = self._queue.pop(0)
            if not task_id:
                time.sleep(0.25)
                continue
            task = self.get(task_id)
            if not task or task.state == SyncState.PAUSED.value:
                continue
            runner = self._runners.get(task.kind)
            if not runner:
                task.state = SyncState.ERROR.value
                task.error = f"No runner for kind={task.kind}"
                task.finished_ms = int(time.time() * 1000)
                continue
            task.state = SyncState.UPLOADING.value if task.kind == "upload" else SyncState.DOWNLOADING.value
            task.started_ms = int(time.time() * 1000)

            def _progress(t: SyncTask) -> None:
                with self._lock:
                    self._tasks[t.task_id] = t

            try:
                runner(task, _progress)
                task.state = SyncState.SYNCED.value
                task.progress = 1.0
            except Exception as e:
                task.state = SyncState.ERROR.value
                task.error = str(e)[:500]
            task.finished_ms = int(time.time() * 1000)
