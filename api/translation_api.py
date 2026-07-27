"""Translation Agent v1.0 API — translation report endpoints."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from flask import Blueprint, jsonify, request

from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

logger = logging.getLogger("tubedub.translation_api")

bp = Blueprint("translation_api", __name__)

_APP_DIR = Path(__file__).resolve().parent.parent
_MANIFESTS_DIR = _APP_DIR / "output" / "manifests"
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{4,64}$")
_RESERVED = frozenset({"reports", "project", "list"})


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def _safe_id(value: str) -> str | None:
    safe = Path(str(value or "")).name.strip()
    if not safe or safe != str(value).strip() or not _ID_RE.match(safe):
        return None
    if safe.lower() in _RESERVED:
        return None
    return safe


def _report_path_for_project(project_uuid: str) -> Path | None:
    safe = _safe_id(project_uuid)
    if not safe:
        return None
    candidate = _MANIFESTS_DIR / safe / "translation_report.json"
    if candidate.is_file():
        return candidate
    return None


def _report_path_for_task(task_id: str) -> Path | None:
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
    if not task:
        return None
    info = task.get("info") or {}
    report_path = info.get("translation_report_path")
    if report_path:
        p = Path(str(report_path))
        try:
            resolved = p.resolve()
            out = (_APP_DIR / "output").resolve()
            if resolved.is_file() and (
                _MANIFESTS_DIR.resolve() in resolved.parents or out in resolved.parents
            ):
                return resolved
        except OSError:
            pass
    project_uuid = info.get("project_uuid")
    if project_uuid:
        return _report_path_for_project(str(project_uuid))
    return None


def _summarize_report(report: dict) -> dict:
    segments = report.get("segments") or report.get("translated_segments") or []
    return {
        "segment_count": report.get("segment_count")
        or (len(segments) if isinstance(segments, list) else 0),
        "source_lang": report.get("source_lang") or report.get("src_lang") or "",
        "target_lang": report.get("target_lang") or report.get("tgt_lang") or "",
        "engine": report.get("engine") or report.get("engines") or "",
        "created_at": report.get("created_at") or report.get("timestamp") or "",
    }


@bp.get("/api/translation/reports")
def api_translation_reports_list():
    """List on-disk translation reports under output/manifests."""
    limit = request.args.get("limit", "50")
    try:
        limit_n = max(1, min(200, int(limit)))
    except ValueError:
        limit_n = 50

    items: list[dict] = []
    if _MANIFESTS_DIR.is_dir():
        for report_path in sorted(
            _MANIFESTS_DIR.glob("*/translation_report.json"),
            key=lambda p: -p.stat().st_mtime,
        ):
            project_uuid = report_path.parent.name
            report = _load_json(report_path) or {}
            items.append(
                {
                    "project_uuid": project_uuid,
                    "report_path": str(report_path),
                    "mtime": report_path.stat().st_mtime,
                    "summary": _summarize_report(report),
                }
            )
            if len(items) >= limit_n:
                break

    return jsonify({"ok": True, "reports": items, "count": len(items)})


@bp.get("/api/translation/project/<project_uuid>")
def api_translation_report_by_project(project_uuid: str):
    """Load translation report by durable project UUID (survives process restart)."""
    report_path = _report_path_for_project(project_uuid)
    if not report_path:
        return jsonify({"ok": False, "error": "translation_report not found"}), 404
    report = _load_json(report_path)
    if not report:
        return jsonify({"ok": False, "error": "translation_report unreadable"}), 404
    safe = _safe_id(project_uuid)
    return jsonify(
        {
            "ok": True,
            "project_uuid": safe,
            "report_path": str(report_path),
            "summary": _summarize_report(report),
            "translation_report": report,
        }
    )


@bp.get("/api/translation/<task_id>")
def api_translation_report(task_id: str):
    safe = _safe_id(task_id)
    if not safe:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report_path = _report_path_for_task(safe)
    if not report_path:
        # Persistence fallback: treat task_id as project_uuid after restart.
        report_path = _report_path_for_project(safe)
    if not report_path:
        return jsonify({"ok": False, "error": "translation_report not found", "task_id": safe}), 404

    report = _load_json(report_path)
    if not report:
        return jsonify({"ok": False, "error": "translation_report unreadable", "task_id": safe}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": safe,
            "report_path": str(report_path),
            "summary": _summarize_report(report),
            "translation_report": report,
        }
    )
