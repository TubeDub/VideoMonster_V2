"""Grammar Agent v1.0 API — grammar report endpoints."""

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

logger = logging.getLogger("tubedub.grammar_api")

bp = Blueprint("grammar_api", __name__)


@bp.get("/api/grammar")
@bp.get("/api/grammar/list")
def api_grammar_list():
    return jsonify({"ok": True, "reports": list_recent_reports("grammar_report.json")})


@bp.get("/api/grammar/<task_id>/summary")
def api_grammar_summary(task_id: str):
    safe = safe_task_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = resolve_report_path(
        safe, info_key="grammar_report_path", filename="grammar_report.json"
    )
    report = load_json(report_path) if report_path else None
    if not report:
        return jsonify({"ok": False, "error": "grammar_report not found", "task_id": safe}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "report_path": str(report_path),
            "summary": report_summary(report, kind="grammar"),
        }
    )


@bp.get("/api/grammar/<task_id>/segment/<segment_id>")
def api_grammar_segment(task_id: str, segment_id: str):
    safe = safe_task_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = resolve_report_path(
        safe, info_key="grammar_report_path", filename="grammar_report.json"
    )
    report = load_json(report_path) if report_path else None
    if not report:
        return jsonify({"ok": False, "error": "grammar_report not found", "task_id": safe}), 404

    item = segment_from_report(report, segment_id)
    if item is None:
        return jsonify({"ok": False, "error": "segment not found", "segment_id": segment_id}), 404
    return jsonify({"ok": True, "task_id": safe, "segment_id": segment_id, "segment": item})


@bp.get("/api/grammar/<task_id>")
def api_grammar_report(task_id: str):
    safe = safe_task_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = resolve_report_path(
        safe, info_key="grammar_report_path", filename="grammar_report.json"
    )
    if not report_path:
        return jsonify({"ok": False, "error": "grammar_report not found", "task_id": safe}), 404

    report = load_json(report_path)
    if not report:
        return jsonify({"ok": False, "error": "grammar_report unreadable", "task_id": safe}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "report_path": str(report_path),
            "grammar_report": report,
            "summary": report_summary(report, kind="grammar"),
        }
    )
