"""Analytics database — project history & timeline storage (TZ #8 §12)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class AnalyticsDB:
    """SQLite store for project runs, timeline, and diagnostics history."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def _init(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS project_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id TEXT NOT NULL,
                        started_at REAL,
                        finished_at REAL,
                        duration_s REAL DEFAULT 0,
                        models TEXT DEFAULT '[]',
                        performance TEXT DEFAULT '{}',
                        errors TEXT DEFAULT '[]',
                        recommendations TEXT DEFAULT '[]',
                        speed REAL DEFAULT 0,
                        status TEXT DEFAULT 'unknown',
                        report_path TEXT DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS timeline_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id TEXT NOT NULL,
                        event_type TEXT DEFAULT '',
                        stage TEXT DEFAULT '',
                        chunk_id INTEGER DEFAULT -1,
                        message TEXT NOT NULL,
                        recorded_at REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_runs_project
                        ON project_runs(project_id);
                    CREATE INDEX IF NOT EXISTS idx_timeline_project
                        ON timeline_events(project_id);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def save_run(
        self,
        project_id: str,
        *,
        started_at: float = 0.0,
        finished_at: float = 0.0,
        duration_s: float = 0.0,
        models: list[str] | None = None,
        performance: dict[str, Any] | None = None,
        errors: list[str] | None = None,
        recommendations: list[dict[str, Any]] | None = None,
        speed: float = 0.0,
        status: str = "completed",
        report_path: str = "",
    ) -> int:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO project_runs "
                    "(project_id, started_at, finished_at, duration_s, models, performance, "
                    "errors, recommendations, speed, status, report_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        project_id,
                        started_at or time.time(),
                        finished_at or time.time(),
                        duration_s,
                        json.dumps(models or [], ensure_ascii=False),
                        json.dumps(performance or {}, ensure_ascii=False),
                        json.dumps(errors or [], ensure_ascii=False),
                        json.dumps(recommendations or [], ensure_ascii=False),
                        speed,
                        status,
                        report_path,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                conn.close()

    def add_timeline(
        self,
        project_id: str,
        message: str,
        *,
        event_type: str = "info",
        stage: str = "",
        chunk_id: int = -1,
        recorded_at: float | None = None,
    ) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO timeline_events "
                    "(project_id, event_type, stage, chunk_id, message, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        project_id,
                        event_type,
                        stage,
                        chunk_id,
                        message,
                        recorded_at or time.time(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_history(self, *, limit: int = 50, project_id: str = "") -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                if project_id:
                    rows = conn.execute(
                        "SELECT id, project_id, started_at, finished_at, duration_s, "
                        "models, performance, errors, recommendations, speed, status "
                        "FROM project_runs WHERE project_id=? ORDER BY finished_at DESC LIMIT ?",
                        (project_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, project_id, started_at, finished_at, duration_s, "
                        "models, performance, errors, recommendations, speed, status "
                        "FROM project_runs ORDER BY finished_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
            finally:
                conn.close()
        return [self._row_to_run(r) for r in rows]

    def get_timeline(self, project_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT event_type, stage, chunk_id, message, recorded_at "
                    "FROM timeline_events WHERE project_id=? "
                    "ORDER BY recorded_at ASC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            finally:
                conn.close()
        return [
            {
                "event_type": r[0],
                "stage": r[1],
                "chunk_id": r[2],
                "message": r[3],
                "recorded_at": r[4],
                "time": time.strftime("%H:%M:%S", time.localtime(r[4])),
            }
            for r in rows
        ]

    @staticmethod
    def _row_to_run(row: tuple) -> dict[str, Any]:
        def _load(val: str, default: Any) -> Any:
            try:
                return json.loads(val)
            except Exception:
                return default

        return {
            "id": row[0],
            "project_id": row[1],
            "started_at": row[2],
            "finished_at": row[3],
            "duration_s": row[4],
            "models": _load(row[5], []),
            "performance": _load(row[6], {}),
            "errors": _load(row[7], []),
            "recommendations": _load(row[8], []),
            "speed": row[9],
            "status": row[10],
        }


def get_analytics_db(app_dir: str | Path | None = None) -> AnalyticsDB:
    base = Path(app_dir) if app_dir else Path.cwd()
    db_dir = os.getenv("VM_ANALYTICS_DIR") or str(base / "data" / "analytics")
    return AnalyticsDB(Path(db_dir) / "analytics.db")
