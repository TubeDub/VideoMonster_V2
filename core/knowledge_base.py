"""Project knowledge base — best practices, lessons, solutions (TZ #10 §12)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


_DEFAULT_ENTRIES = [
    {
        "category": "architecture",
        "title": "Never modify core pipeline algorithms",
        "content": "Stages 1-9 core modules (Event Bus, Orchestrator, Pipeline Engine) "
                   "must not be changed when adding features — extend via plugins and wrappers.",
        "tags": ["architecture", "plugins"],
    },
    {
        "category": "performance",
        "title": "Dynamic resource planning",
        "content": "Never use fixed worker counts — derive from Hardware Profiler + Benchmark.",
        "tags": ["performance", "optimizer"],
    },
    {
        "category": "memory",
        "title": "User corrections are canonical",
        "content": "Locked glossary entries in AI Memory cannot be overwritten by auto-learn.",
        "tags": ["memory", "translation"],
    },
    {
        "category": "reliability",
        "title": "Fault isolation per chunk",
        "content": "Recovery Manager retries lines first, then chunks, then parks — never abort film.",
        "tags": ["recovery", "pipeline"],
    },
]


class KnowledgeBase:
    """Internal knowledge store for all AI development tools."""

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
                    CREATE TABLE IF NOT EXISTS entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        tags TEXT DEFAULT '[]',
                        source TEXT DEFAULT 'builtin',
                        recorded_at REAL,
                        hit_count INTEGER DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS idx_kb_category ON entries(category);
                    CREATE INDEX IF NOT EXISTS idx_kb_tags ON entries(tags);
                    """
                )
                count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                if count == 0:
                    for e in _DEFAULT_ENTRIES:
                        conn.execute(
                            "INSERT INTO entries (category, title, content, tags, source, recorded_at) "
                            "VALUES (?, ?, ?, ?, 'builtin', ?)",
                            (e["category"], e["title"], e["content"],
                             json.dumps(e["tags"]), time.time()),
                        )
                conn.commit()
            finally:
                conn.close()

    def add(
        self,
        category: str,
        title: str,
        content: str,
        *,
        tags: list[str] | None = None,
        source: str = "assistant",
    ) -> int:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO entries (category, title, content, tags, source, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (category, title, content, json.dumps(tags or []), source, time.time()),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                conn.close()

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        q = f"%{query}%"
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT id, category, title, content, tags, source, hit_count "
                    "FROM entries WHERE title LIKE ? OR content LIKE ? OR tags LIKE ? "
                    "ORDER BY hit_count DESC LIMIT ?",
                    (q, q, q, limit),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        "UPDATE entries SET hit_count=hit_count+1 WHERE id=?", (row[0],)
                    )
                conn.commit()
            finally:
                conn.close()
        return [
            {
                "id": r[0], "category": r[1], "title": r[2], "content": r[3],
                "tags": json.loads(r[4] or "[]"), "source": r[5], "hit_count": r[6],
            }
            for r in rows
        ]

    def by_category(self, category: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT id, category, title, content, tags FROM entries "
                    "WHERE category=? ORDER BY hit_count DESC LIMIT ?",
                    (category, limit),
                ).fetchall()
            finally:
                conn.close()
        return [
            {"id": r[0], "category": r[1], "title": r[2], "content": r[3],
             "tags": json.loads(r[4] or "[]")}
            for r in rows
        ]

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            conn = self._conn()
            try:
                total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                cats = conn.execute(
                    "SELECT category, COUNT(*) FROM entries GROUP BY category"
                ).fetchall()
            finally:
                conn.close()
        return {"total": total, "categories": {c: n for c, n in cats}}


_kb: KnowledgeBase | None = None
_kb_lock = threading.Lock()


def get_knowledge_base(app_dir: str | Path | None = None) -> KnowledgeBase:
    global _kb
    if _kb is None:
        with _kb_lock:
            if _kb is None:
                base = Path(app_dir) if app_dir else Path.cwd()
                db_dir = os.getenv("VM_KB_DIR") or str(base / "data" / "knowledge")
                _kb = KnowledgeBase(Path(db_dir) / "knowledge_base.db")
    return _kb
