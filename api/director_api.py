"""Director Agent v1.0 API — creative brief report endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify

from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

logger = logging.getLogger("tubedub.director_api")

bp = Blueprint("director_api", __name__)

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


def _manifest_dir_for_task(task_id: str) -> Path | None:
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
    if not task:
        return None
    info = task.get("info") or {}
    report_path = info.get("director_report_path")
    if report_path:
        p = Path(report_path)
        if p.is_file():
            return p.parent
    manifest_path = info.get("manifest_path") or task.get("manifest_path")
    if manifest_path:
        p = Path(manifest_path)
        if p.is_file():
            return p.parent
    project_uuid = info.get("project_uuid") or task.get("project_uuid")
    if project_uuid:
        d = _MANIFESTS_DIR / project_uuid
        if d.is_dir():
            return d
    return None


@bp.get("/api/director/<task_id>")
def api_director_by_task(task_id: str):
    safe = Path(task_id).name
    if not safe or safe != task_id:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    manifest_dir = _manifest_dir_for_task(safe)
    if not manifest_dir:
        return jsonify({"ok": False, "error": "director report not found", "task_id": safe}), 404

    report = _load_json(manifest_dir / "director_report.json")
    if not report:
        return jsonify({"ok": False, "error": "director report not found", "task_id": safe}), 404

    with STATE_LOCK:
        task = AUTO_TASKS.get(safe) or {}
        info = task.get("info") or {}
        creative_briefs = info.get("creative_briefs") or report.get("per_segment") or []

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "project_uuid": report.get("project_uuid"),
            "director_report": report,
            "creative_briefs": creative_briefs,
            "director_status": info.get("director_agent_status") or report.get("status"),
        }
    )
