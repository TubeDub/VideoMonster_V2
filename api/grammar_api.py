"""Grammar Agent v1.0 API — grammar report endpoint."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify

from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

logger = logging.getLogger("tubedub.grammar_api")

bp = Blueprint("grammar_api", __name__)

_APP_DIR = Path(__file__).resolve().parent.parent
_MANIFESTS_DIR = _APP_DIR / "output" / "manifests"


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _report_path_for_task(task_id: str) -> Path | None:
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
    if not task:
        return None
    info = task.get("info") or {}
    report_path = info.get("grammar_report_path")
    if report_path:
        p = Path(report_path)
        if p.is_file():
            return p
    project_uuid = info.get("project_uuid")
    if project_uuid:
        candidate = _MANIFESTS_DIR / project_uuid / "grammar_report.json"
        if candidate.is_file():
            return candidate
    return None


@bp.get("/api/grammar/<task_id>")
def api_grammar_report(task_id: str):
    safe = Path(task_id).name
    if not safe or safe != task_id:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = _report_path_for_task(safe)
    if not report_path:
        return jsonify({"ok": False, "error": "grammar_report not found", "task_id": safe}), 404

    report = _load_json(report_path)
    if not report:
        return jsonify({"ok": False, "error": "grammar_report unreadable", "task_id": safe}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "report_path": str(report_path),
            "grammar_report": report,
        }
    )
