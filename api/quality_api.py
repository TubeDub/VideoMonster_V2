"""Quality Agent v1.0 API — quality report endpoint."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify

from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

logger = logging.getLogger("tubedub.quality_api")

bp = Blueprint("quality_api", __name__)

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
    report_path = info.get("quality_report_path")
    if report_path:
        p = Path(report_path)
        if p.is_file():
            return p
    project_uuid = info.get("project_uuid")
    if project_uuid:
        candidate = _MANIFESTS_DIR / project_uuid / "quality_report.json"
        if candidate.is_file():
            return candidate
    return None


def _tps_metrics_for_task(task_id: str) -> tuple[dict | None, str | None]:
    """Return (metrics_dict, source_path_or_label)."""
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        info = (task.get("info") or {}) if task else {}
        live = info.get("tps_metrics")
        if isinstance(live, dict) and live:
            return live, "task.info.tps_metrics"

    session = _APP_DIR / "output" / "sessions" / task_id / "tps_metrics.json"
    hit = _load_json(session)
    if hit:
        return hit, str(session)

    analytics = _APP_DIR / "quality" / "analytics" / f"tps_metrics_{task_id}.json"
    hit = _load_json(analytics)
    if hit:
        return hit, str(analytics)

    return None, None


@bp.get("/api/quality/<task_id>")
def api_quality_report(task_id: str):
    safe = Path(task_id).name
    if not safe or safe != task_id:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = _report_path_for_task(safe)
    if not report_path:
        return jsonify({"ok": False, "error": "quality_report not found", "task_id": safe}), 404

    report = _load_json(report_path)
    if not report:
        return jsonify({"ok": False, "error": "quality_report unreadable", "task_id": safe}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "report_path": str(report_path),
            "quality_report": report,
        }
    )


@bp.get("/api/tps/metrics/<task_id>")
def api_tps_metrics(task_id: str):
    """TPS Fast Path metrics (Fast/Retry/Judge/Manual + latency)."""
    safe = Path(task_id).name
    if not safe or safe != task_id:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    metrics, source = _tps_metrics_for_task(safe)
    if not metrics:
        return jsonify({"ok": False, "error": "tps_metrics not found", "task_id": safe}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "source": source,
            "metrics": metrics,
        }
    )


@bp.get("/api/tps/metrics")
def api_tps_metrics_latest():
    """Latest in-memory task with TPS metrics (for Monitoring Center)."""
    latest_id = None
    latest_metrics = None
    with STATE_LOCK:
        for tid, task in AUTO_TASKS.items():
            info = task.get("info") or {}
            m = info.get("tps_metrics")
            if isinstance(m, dict) and m:
                latest_id = tid
                latest_metrics = m
    if not latest_metrics:
        analytics_dir = _APP_DIR / "quality" / "analytics"
        if analytics_dir.is_dir():
            files = sorted(
                analytics_dir.glob("tps_metrics_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files:
                latest_metrics = _load_json(files[0])
                latest_id = files[0].stem.replace("tps_metrics_", "", 1)
    if not latest_metrics:
        return jsonify({"ok": False, "error": "no tps_metrics yet"}), 404
    return jsonify({"ok": True, "task_id": latest_id, "metrics": latest_metrics})

