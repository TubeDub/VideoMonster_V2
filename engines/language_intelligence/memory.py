"""Persistent memory — learning_rules.json + optional SQLite stats."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CONN_PATH: str | None = None


def module_data_dir(app_dir: Path | None = None) -> Path:
    base = app_dir or Path(__file__).resolve().parent.parent.parent
    d = base / "data" / "language_intelligence"
    d.mkdir(parents=True, exist_ok=True)
    return d


def learning_rules_path(app_dir: Path | None = None) -> Path:
    return module_data_dir(app_dir) / "learning_rules.json"


def db_path(app_dir: Path | None = None) -> Path:
    return module_data_dir(app_dir) / "language_memory.db"


def close_db() -> None:
    global _CONN, _CONN_PATH
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
        _CONN = None
        _CONN_PATH = None


def _get_db(app_dir: Path | None = None) -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    path = str(db_path(app_dir))
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    close_db()
    _CONN = sqlite3.connect(path, check_same_thread=False)
    _CONN_PATH = path
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS correction_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_lang TEXT,
            tgt_lang TEXT,
            before_text TEXT,
            after_text TEXT,
            category TEXT,
            count INTEGER DEFAULT 1,
            success_count INTEGER DEFAULT 1,
            last_seen REAL,
            UNIQUE(before_text, after_text, tgt_lang)
        )
        """
    )
    _CONN.commit()
    return _CONN


def _default_rules_doc() -> dict[str, Any]:
    return {
        "version": 1,
        "candidates": [],
        "permanent": [],
        "stats": {"total_jobs": 0, "total_corrections": 0},
    }


def load_learning_rules(app_dir: Path | None = None) -> dict[str, Any]:
    path = learning_rules_path(app_dir)
    if not path.is_file():
        doc = _default_rules_doc()
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        return doc
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else _default_rules_doc()
    except Exception:
        return _default_rules_doc()


def save_learning_rules(doc: dict[str, Any], app_dir: Path | None = None) -> None:
    path = learning_rules_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def permanent_rules(app_dir: Path | None = None) -> list[dict[str, Any]]:
    doc = load_learning_rules(app_dir)
    return list(doc.get("permanent") or [])


def record_correction_stat(
    before: str,
    after: str,
    *,
    category: str = "learned",
    src_lang: str = "en",
    tgt_lang: str = "uk",
    success: bool = True,
    app_dir: Path | None = None,
) -> None:
    if before == after or not before or not after:
        return
    with _LOCK:
        conn = _get_db(app_dir)
        now = time.time()
        row = conn.execute(
            "SELECT count, success_count FROM correction_stats "
            "WHERE before_text=? AND after_text=? AND tgt_lang=?",
            (before, after, tgt_lang),
        ).fetchone()
        if row:
            cnt, succ = row
            cnt += 1
            succ += 1 if success else 0
            conn.execute(
                "UPDATE correction_stats SET count=?, success_count=?, last_seen=? "
                "WHERE before_text=? AND after_text=? AND tgt_lang=?",
                (cnt, succ, now, before, after, tgt_lang),
            )
        else:
            conn.execute(
                "INSERT INTO correction_stats "
                "(src_lang, tgt_lang, before_text, after_text, category, count, success_count, last_seen) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (src_lang, tgt_lang, before, after, category, 1 if success else 0, now),
            )
        conn.commit()


def fetch_correction_stats(
    *,
    tgt_lang: str = "uk",
    min_count: int = 1,
    app_dir: Path | None = None,
) -> list[dict[str, Any]]:
    with _LOCK:
        conn = _get_db(app_dir)
        rows = conn.execute(
            "SELECT before_text, after_text, category, count, success_count "
            "FROM correction_stats WHERE tgt_lang=? AND count>=? ORDER BY count DESC",
            (tgt_lang, min_count),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for b, a, cat, cnt, succ in rows:
        out.append(
            {
                "before": b,
                "after": a,
                "category": cat,
                "count": cnt,
                "success_count": succ,
                "confidence": round(succ / max(cnt, 1), 3),
            }
        )
    return out
