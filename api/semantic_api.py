"""Semantic Agent v1.0 API — semantic report endpoints."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from api._agent_report_helpers import (
    list_recent_reports,
    load_json,
    report_summary,
    resolve_report_path,
    safe_task_id,
    segment_from_report,
)

logger = logging.getLogger("tubedub.semantic_api")

bp = Blueprint("semantic_api", __name__)


@bp.get("/api/semantic")
@bp.get("/api/semantic/list")
def api_semantic_list():
    return jsonify({"ok": True, "reports": list_recent_reports("semantic_report.json")})


@bp.get("/api/semantic/<task_id>/summary")
def api_semantic_summary(task_id: str):
    safe = safe_task_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = resolve_report_path(
        safe, info_key="semantic_report_path", filename="semantic_report.json"
    )
    report = load_json(report_path) if report_path else None
    if not report:
        return jsonify({"ok": False, "error": "semantic_report not found", "task_id": safe}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "report_path": str(report_path),
            "summary": report_summary(report, kind="semantic"),
        }
    )


@bp.get("/api/semantic/<task_id>/segment/<segment_id>")
def api_semantic_segment(task_id: str, segment_id: str):
    safe = safe_task_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = resolve_report_path(
        safe, info_key="semantic_report_path", filename="semantic_report.json"
    )
    report = load_json(report_path) if report_path else None
    if not report:
        return jsonify({"ok": False, "error": "semantic_report not found", "task_id": safe}), 404

    item = segment_from_report(report, segment_id)
    if item is None:
        return jsonify({"ok": False, "error": "segment not found", "segment_id": segment_id}), 404
    return jsonify({"ok": True, "task_id": safe, "segment_id": segment_id, "segment": item})


@bp.get("/api/semantic/<task_id>")
def api_semantic_report(task_id: str):
    safe = safe_task_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = resolve_report_path(
        safe, info_key="semantic_report_path", filename="semantic_report.json"
    )
    if not report_path:
        return jsonify({"ok": False, "error": "semantic_report not found", "task_id": safe}), 404

    report = load_json(report_path)
    if not report:
        return jsonify({"ok": False, "error": "semantic_report unreadable", "task_id": safe}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "report_path": str(report_path),
            "semantic_report": report,
            "summary": report_summary(report, kind="semantic"),
        }
    )
