"""Dev pipeline API — developer mode only (TZ §2)."""

from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request

from engines.core.feature_flags import is_developer
from engines.core.events import get_event_bus
from engines.core.module_registry import readiness_table
from engines.ai_director import format_report

APP_DIR = Path(__file__).resolve().parent.parent
bp = Blueprint("dev_api", __name__)


def _dev_guard():
    if not is_developer(
        request_headers=dict(request.headers),
        request_cookies=dict(request.cookies),
    ):
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    return None


@bp.get("/api/dev/pipeline/<task_id>")
def api_dev_pipeline_task(task_id: str):
    blocked = _dev_guard()
    if blocked:
        return blocked
    from api.auto_dub_api import AUTO_TASKS, STATE_LOCK

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
    if not task:
        return jsonify({"ok": False, "error": "Task not found"}), 404
    info = task.get("info") or {}
    director = info.get("ai_director_report") or {}
    return jsonify(
        {
            "ok": True,
            "task_id": task_id,
            "step": task.get("step"),
            "progress": task.get("progress"),
            "stages": info.get("pipeline_stages") or [],
            "word_timing": {
                "meta": info.get("word_timing_meta"),
                "maps": info.get("source_word_maps") or info.get("merged_word_maps"),
                "checkpoints": info.get("word_timing_checkpoints"),
            },
            "director": director,
            "emotions": info.get("emotion_tags"),
            "events": get_event_bus().history(limit=50),
            "timing_warnings": info.get("timing_warnings"),
        }
    )


@bp.get("/api/dev/pipeline/<task_id>/report")
def api_dev_pipeline_report(task_id: str):
    blocked = _dev_guard()
    if blocked:
        return blocked
    from api.auto_dub_api import AUTO_TASKS, STATE_LOCK

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
    if not task:
        return jsonify({"ok": False, "error": "Task not found"}), 404
    info = task.get("info") or {}
    director = info.get("ai_director_report") or {}
    text = info.get("ai_director_text") or ""
    if not text and director:
        from engines.ai_director import QualityScore, QualityIssue

        issues = [QualityIssue(**i) if isinstance(i, dict) else i for i in director.get("issues", [])]
        qs = QualityScore(
            score=float(director.get("score", 0)),
            block_export=bool(director.get("block_export")),
            issues=issues,
            checks=director.get("checks") or {},
        )
        text = format_report(qs)
    return jsonify({"ok": True, "task_id": task_id, "report": director, "text": text})


@bp.get("/api/dev/modules/readiness")
def api_dev_modules_readiness():
    blocked = _dev_guard()
    if blocked:
        return blocked
    return jsonify({"ok": True, "modules": readiness_table(APP_DIR)})


@bp.get("/api/dev/events")
def api_dev_events():
    blocked = _dev_guard()
    if blocked:
        return blocked
    stage = request.args.get("stage")
    limit = min(200, int(request.args.get("limit", 100)))
    return jsonify({"ok": True, "events": get_event_bus().history(stage=stage, limit=limit)})
