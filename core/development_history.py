"""Development history database (TZ #10 §11)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class DevelopmentHistoryDB:
    """SQLite store for architectural changes, decisions, test results."""

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
                    CREATE TABLE IF NOT EXISTS changes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        files TEXT DEFAULT '[]',
                        reason TEXT DEFAULT '',
                        impact TEXT DEFAULT '{}',
                        test_results TEXT DEFAULT '{}',
                        performance_delta TEXT DEFAULT '{}',
                        recorded_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        topic TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        rationale TEXT DEFAULT '',
                        alternatives TEXT DEFAULT '[]',
                        recorded_at REAL
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def record_change(
        self,
        title: str,
        *,
        files: list[str] | None = None,
        reason: str = "",
        impact: dict[str, Any] | None = None,
        test_results: dict[str, Any] | None = None,
        performance_delta: dict[str, Any] | None = None,
    ) -> int:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO changes (title, files, reason, impact, test_results, "
                    "performance_delta, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        title,
                        json.dumps(files or [], ensure_ascii=False),
                        reason,
                        json.dumps(impact or {}, ensure_ascii=False),
                        json.dumps(test_results or {}, ensure_ascii=False),
                        json.dumps(performance_delta or {}, ensure_ascii=False),
                        time.time(),
                    ),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                conn.close()

    def record_decision(
        self,
        topic: str,
        decision: str,
        *,
        rationale: str = "",
        alternatives: list[str] | None = None,
    ) -> int:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO decisions (topic, decision, rationale, alternatives, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        topic,
                        decision,
                        rationale,
                        json.dumps(alternatives or [], ensure_ascii=False),
                        time.time(),
                    ),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                conn.close()

    def recent_changes(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT id, title, files, reason, impact, test_results, "
                    "performance_delta, recorded_at FROM changes "
                    "ORDER BY recorded_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            finally:
                conn.close()
        return [self._parse_change(r) for r in rows]

    def recent_decisions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT id, topic, decision, rationale, alternatives, recorded_at "
                    "FROM decisions ORDER BY recorded_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            finally:
                conn.close()
        return [
            {
                "id": r[0],
                "topic": r[1],
                "decision": r[2],
                "rationale": r[3],
                "alternatives": json.loads(r[4] or "[]"),
                "recorded_at": r[5],
            }
            for r in rows
        ]

    @staticmethod
    def _parse_change(row: tuple) -> dict[str, Any]:
        return {
            "id": row[0],
            "title": row[1],
            "files": json.loads(row[2] or "[]"),
            "reason": row[3],
            "impact": json.loads(row[4] or "{}"),
            "test_results": json.loads(row[5] or "{}"),
            "performance_delta": json.loads(row[6] or "{}"),
            "recorded_at": row[7],
        }


def get_development_history(app_dir: str | Path | None = None) -> DevelopmentHistoryDB:
    base = Path(app_dir) if app_dir else Path.cwd()
    db_dir = os.getenv("VM_DEV_HISTORY_DIR") or str(base / "data" / "development")
    return DevelopmentHistoryDB(Path(db_dir) / "development_history.db")
