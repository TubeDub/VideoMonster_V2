"""Scheduled project backups."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from engines.cloud.models import BackupSchedule
from engines.cloud.store import CloudStore


class BackupScheduler:
    INTERVALS = {
        BackupSchedule.EVERY_30_MIN.value: 30 * 60,
        BackupSchedule.HOURLY.value: 3600,
        BackupSchedule.DAILY.value: 86400,
    }

    def __init__(self, app_dir: Path, store: CloudStore, enqueue_backup):
        self.app_dir = Path(app_dir)
        self.store = store
        self._enqueue = enqueue_backup
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cloud-backup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        last_run = 0.0
        while not self._stop.is_set():
            settings = self.store.load_settings()
            sched = str(settings.get("backup_schedule") or BackupSchedule.MANUAL.value)
            interval = self.INTERVALS.get(sched)
            if interval and time.time() - last_run >= interval:
                try:
                    self._enqueue()
                    last_run = time.time()
                except Exception:
                    pass
            self._stop.wait(15)

    def snapshot(self) -> dict[str, Any]:
        settings = self.store.load_settings()
        return {
            "schedule": settings.get("backup_schedule", BackupSchedule.MANUAL.value),
            "intervals": list(self.INTERVALS.keys()),
        }
