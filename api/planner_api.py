"""Planner Agent v3.0 API — manifest and report endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify

from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

logger = logging.getLogger("tubedub.planner_api")

bp = Blueprint("planner_api", __name__)

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


@bp.get("/api/planner/<task_id>")
def api_planner_by_task(task_id: str):
    safe = Path(task_id).name
    if not safe or safe != task_id:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    manifest_dir = _manifest_dir_for_task(safe)
    if not manifest_dir:
        return jsonify({"ok": False, "error": "manifest not found", "task_id": safe}), 404

    manifest = _load_json(manifest_dir / "project_manifest.json")
    report = _load_json(manifest_dir / "planner_report.json")
    if not manifest:
        return jsonify({"ok": False, "error": "manifest not found", "task_id": safe}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "project_uuid": manifest.get("project_uuid"),
            "manifest": manifest,
            "planner_report": report,
        }
    )


@bp.get("/api/planner/manifest/<project_uuid>")
def api_planner_by_uuid(project_uuid: str):
    safe = Path(project_uuid).name
    if not safe or safe != project_uuid:
        return jsonify({"ok": False, "error": "invalid project_uuid"}), 400

    manifest_dir = _MANIFESTS_DIR / safe
    manifest = _load_json(manifest_dir / "project_manifest.json")
    report = _load_json(manifest_dir / "planner_report.json")
    if not manifest:
        return jsonify({"ok": False, "error": "manifest not found", "project_uuid": safe}), 404

    return jsonify(
        {
            "ok": True,
            "project_uuid": safe,
            "manifest": manifest,
            "planner_report": report,
        }
    )
