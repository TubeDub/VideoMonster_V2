"""Director Agent v1.0 API — creative brief report endpoints."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from api._agent_report_helpers import (
    list_recent_reports,
    load_json,
    report_summary,
    resolve_manifest_dir,
    resolve_report_path,
    safe_task_id,
    segment_from_report,
    task_info,
)

logger = logging.getLogger("tubedub.director_api")

bp = Blueprint("director_api", __name__)


@bp.get("/api/director")
@bp.get("/api/director/list")
def api_director_list():
    """List recent director reports on disk."""
    return jsonify({"ok": True, "reports": list_recent_reports("director_report.json")})


@bp.get("/api/director/<task_id>/summary")
def api_director_summary(task_id: str):
    safe = safe_task_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = resolve_report_path(
        safe, info_key="director_report_path", filename="director_report.json"
    )
    if not report_path:
        manifest_dir = resolve_manifest_dir(safe)
        if manifest_dir:
            report_path = manifest_dir / "director_report.json"
    report = load_json(report_path) if report_path else None
    if not report:
        return jsonify({"ok": False, "error": "director report not found", "task_id": safe}), 404

    info = task_info(safe)
    summary = report_summary(report, kind="director")
    summary["director_status"] = info.get("director_agent_status") or summary.get("status")
    summary["llm_used_count"] = report.get("llm_used_count")
    summary["rule_only_count"] = report.get("rule_only_count")
    return jsonify({"ok": True, "task_id": safe, "summary": summary, "report_path": str(report_path)})


@bp.get("/api/director/<task_id>/segment/<segment_id>")
def api_director_segment(task_id: str, segment_id: str):
    safe = safe_task_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = resolve_report_path(
        safe, info_key="director_report_path", filename="director_report.json"
    )
    if not report_path:
        manifest_dir = resolve_manifest_dir(safe)
        if manifest_dir:
            report_path = manifest_dir / "director_report.json"
    report = load_json(report_path) if report_path else None
    if not report:
        return jsonify({"ok": False, "error": "director report not found", "task_id": safe}), 404

    brief = segment_from_report(report, segment_id)
    if brief is None:
        return jsonify({"ok": False, "error": "segment not found", "segment_id": segment_id}), 404
    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "segment_id": segment_id,
            "creative_brief": brief,
        }
    )


@bp.get("/api/director/<task_id>")
def api_director_by_task(task_id: str):
    safe = safe_task_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = resolve_report_path(
        safe, info_key="director_report_path", filename="director_report.json"
    )
    if not report_path:
        manifest_dir = resolve_manifest_dir(safe)
        if manifest_dir:
            candidate = manifest_dir / "director_report.json"
            if candidate.is_file():
                report_path = candidate

    report = load_json(report_path) if report_path else None
    if not report:
        return jsonify({"ok": False, "error": "director report not found", "task_id": safe}), 404

    info = task_info(safe)
    creative_briefs = info.get("creative_briefs") or report.get("per_segment") or []

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "project_uuid": report.get("project_uuid"),
            "report_path": str(report_path) if report_path else None,
            "director_report": report,
            "creative_briefs": creative_briefs,
            "director_status": info.get("director_agent_status") or report.get("status"),
            "summary": report_summary(report, kind="director"),
        }
    )


@bp.post("/api/director/validate")
def api_director_validate():
    """Run AI Director quality checklist on supplied segments."""
    data = request.get_json(silent=True) or {}
    source = list(data.get("source_segments") or [])
    translated = list(data.get("translated_segments") or [])
    timing = list(data.get("timing_map") or [])
    # Soft crash guard — reject pathological payloads before scoring.
    if len(source) > 5000 or len(translated) > 5000 or len(timing) > 5000:
        return jsonify({"ok": False, "error": "payload_too_large"}), 400
    try:
        from engines.ai_director import validate_pipeline

        score = validate_pipeline(
            source_segments=[str(s) for s in source],
            translated_segments=[str(s) for s in translated],
            timing_map=timing,
            word_maps=data.get("word_maps"),
            timing_warnings=data.get("timing_warnings"),
            min_score_to_export=float(data.get("min_score_to_export") or 0.55),
        )
        return jsonify({"ok": True, "quality": score.to_dict()})
    except Exception as exc:
        logger.exception("director validate failed")
        return jsonify({"ok": False, "error": str(exc)}), 500
