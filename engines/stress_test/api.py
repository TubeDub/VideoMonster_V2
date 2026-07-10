"""Flask API for Stress Test Center."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from engines.stress_test.batch import get_batch, start_batch
from engines.stress_test.config import app_dir, reports_dir
from engines.stress_test.discovery import list_test_videos
from engines.stress_test.guards import allow_stress_test_request
from engines.stress_test.history import history_dir

bp = Blueprint("stress_test_api", __name__)


def _guard():
    if not allow_stress_test_request(request):
        return jsonify({"error": "Stress Test доступен только в режиме Developer / Owner"}), 403
    return None


@bp.get("/api/stress-test/access")
def api_stress_access():
    from engines.mt.translate_guard import is_dev_mode
    from engines.owner_first_run import is_owner_host

    allowed = allow_stress_test_request(request)
    return jsonify(
        {
            "allowed": allowed,
            "dev_mode": is_dev_mode(),
            "owner_host": is_owner_host(),
            "ui_dev": (request.headers.get("X-VM-Ui-Mode") or "").strip().lower() == "dev",
        }
    )


@bp.get("/api/stress-test/videos")
def api_stress_videos():
    err = _guard()
    if err:
        return err
    base = app_dir()
    return jsonify({"videos": list_test_videos(base), "count": len(list_test_videos(base))})


@bp.post("/api/stress-test/start")
def api_stress_start():
    err = _guard()
    if err:
        return err
    batch = start_batch(base=app_dir())
    return jsonify(batch)


@bp.get("/api/stress-test/status/<batch_id>")
def api_stress_status(batch_id: str):
    err = _guard()
    if err:
        return err
    batch = get_batch(batch_id)
    if not batch:
        return jsonify({"error": "batch_not_found"}), 404
    total = int(batch.get("total") or 0)
    current = int(batch.get("current_index") or 0)
    remaining = max(0, total - current) if batch.get("status") == "running" else 0
    return jsonify(
        {
            **batch,
            "remaining": remaining,
            "progress_pct": round(current / total * 100, 1) if total else 0,
        }
    )


@bp.get("/api/stress-test/report")
def api_stress_report_file():
    err = _guard()
    if err:
        return err
    fmt = (request.args.get("format") or "html").lower()
    base = app_dir()
    if fmt == "txt":
        path = reports_dir(base) / "STRESS_TEST_REPORT.txt"
    else:
        path = reports_dir(base) / "STRESS_TEST_REPORT.html"
    if not path.is_file():
        return jsonify({"error": "report_not_found"}), 404
    return send_file(path, as_attachment=False)


@bp.get("/api/stress-test/history")
def api_stress_history():
    err = _guard()
    if err:
        return err
    idx = history_dir(app_dir()) / "index.json"
    if not idx.is_file():
        return jsonify({"history": []})
    import json

    try:
        return jsonify({"history": json.loads(idx.read_text(encoding="utf-8"))})
    except Exception:
        return jsonify({"history": []})
