"""Storage audit & safe temp cleanup API (TubeDub Storage TZ §8–§10)."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger("tubedub.api.storage")

bp = Blueprint("storage_api", __name__)

APP_DIR = Path(__file__).resolve().parents[1]


@bp.get("/api/storage/audit")
def api_storage_audit():
    """Return disk usage breakdown for Settings «Хранилище»."""
    from engines.storage_audit import audit_storage

    try:
        report = audit_storage(APP_DIR)
        return jsonify({"ok": True, **report})
    except Exception as exc:
        logger.exception("storage audit failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/storage/cleanup")
def api_storage_cleanup():
    """Manual cleanup — temp files and caches ONLY (never projects/models/MP4).

    Body: { "scope": "all" | "temp" | "pipeline_cache" | "llm_cache", "confirm": true }
    """
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify(
            {
                "ok": False,
                "error": "confirmation_required",
                "message": "Подтвердите очистку: передайте confirm=true",
            }
        ), 400

    scope = str(data.get("scope") or "all").strip().lower()
    from engines.storage_cleanup import (
        StorageCleanupReport,
        cleanup_all_temp_and_cache,
        cleanup_llm_rewrite_cache,
        cleanup_pipeline_cache,
        cleanup_pipeline_temp,
    )

    report: StorageCleanupReport
    if scope == "temp":
        report = cleanup_pipeline_temp(APP_DIR)
    elif scope in ("pipeline_cache", "cache"):
        report = cleanup_pipeline_cache(APP_DIR)
    elif scope in ("llm_cache", "llm"):
        report = cleanup_llm_rewrite_cache(APP_DIR)
    else:
        report = cleanup_all_temp_and_cache(APP_DIR)

    return jsonify({"ok": True, "storage_report": report.to_dict()})


# ── Storage Manager API (Phase 1) ─────────────────────────────────────


@bp.get("/api/storage/projects")
def api_storage_list_projects():
    """List projects managed by StorageManager."""
    from engines.storage.manager import get_storage_manager, list_projects

    include_trashed = request.args.get("trashed", "").lower() in ("1", "true", "yes")
    mgr = get_storage_manager(APP_DIR)
    return jsonify(
        {
            "ok": True,
            "projects": list_projects(include_trashed=include_trashed, app_dir=APP_DIR),
            "statistics": mgr.get_statistics(),
        }
    )


@bp.post("/api/storage/projects")
def api_storage_create_project():
    """Create a new project."""
    from engines.storage.manager import create_project

    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "New Project")
    record = create_project(title=title, app_dir=APP_DIR)
    return jsonify({"ok": True, "project": record.to_dict()})


@bp.get("/api/storage/projects/<project_id>")
def api_storage_get_project(project_id: str):
    from engines.storage.manager import get_storage_manager

    record = get_storage_manager(APP_DIR).get_project(project_id)
    if not record:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "project": record.to_dict()})


@bp.post("/api/storage/projects/<project_id>/open")
def api_storage_open_project(project_id: str):
    from engines.storage.locks import StorageLockError
    from engines.storage.manager import open_project

    try:
        record = open_project(project_id, app_dir=APP_DIR)
        return jsonify({"ok": True, "project": record.to_dict()})
    except StorageLockError as exc:
        return jsonify({"ok": False, "error": "locked", "holder": exc.holder}), 423
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "not_found"}), 404


@bp.post("/api/storage/projects/<project_id>/save")
def api_storage_save_project(project_id: str):
    from engines.storage.manager import save_project

    data = request.get_json(silent=True) or {}
    try:
        record = save_project(project_id, data, app_dir=APP_DIR)
        return jsonify({"ok": True, "project": record.to_dict()})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "not_found"}), 404


@bp.post("/api/storage/projects/<project_id>/close")
def api_storage_close_project(project_id: str):
    from engines.storage.manager import close_project

    close_project(project_id, app_dir=APP_DIR)
    return jsonify({"ok": True})


@bp.post("/api/storage/projects/<project_id>/trash")
def api_storage_trash_project(project_id: str):
    from engines.storage.manager import move_to_trash

    ok = move_to_trash(project_id, app_dir=APP_DIR)
    return jsonify({"ok": ok})


@bp.post("/api/storage/projects/<project_id>/restore")
def api_storage_restore_project(project_id: str):
    from engines.storage.manager import restore_project

    try:
        record = restore_project(project_id, app_dir=APP_DIR)
        return jsonify({"ok": True, "project": record.to_dict()})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "not_in_trash"}), 404


@bp.delete("/api/storage/projects/<project_id>")
def api_storage_delete_project(project_id: str):
    from engines.storage.manager import delete_project

    permanent = request.args.get("permanent", "").lower() in ("1", "true", "yes")
    ok = delete_project(project_id, permanent=permanent, app_dir=APP_DIR)
    return jsonify({"ok": ok})


@bp.post("/api/storage/trash/empty")
def api_storage_empty_trash():
    from engines.storage.manager import empty_trash

    count = empty_trash(app_dir=APP_DIR)
    return jsonify({"ok": True, "deleted": count})


@bp.get("/api/storage/trash")
def api_storage_list_trash():
    from engines.storage.manager import get_storage_manager

    return jsonify({"ok": True, "projects": get_storage_manager(APP_DIR).list_trash()})


@bp.get("/api/storage/recovery")
def api_storage_recovery():
    """Check if a session can be recovered after crash."""
    from engines.storage.manager import check_session_recovery

    recovery = check_session_recovery(APP_DIR)
    return jsonify({"ok": True, "recovery": recovery})


@bp.get("/api/storage/statistics")
def api_storage_statistics():
    from engines.storage.manager import get_statistics

    return jsonify({"ok": True, "statistics": get_statistics(APP_DIR)})


@bp.post("/api/storage/projects/<project_id>/export")
def api_storage_export_project(project_id: str):
    from engines.storage.manager import export_project

    data = request.get_json(silent=True) or {}
    dest = data.get("dest") or str(APP_DIR / "output" / f"{project_id}.vmproj.zip")
    try:
        path = export_project(project_id, dest, app_dir=APP_DIR)
        return jsonify({"ok": True, "path": str(path)})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "not_found"}), 404


@bp.post("/api/storage/projects/import")
def api_storage_import_project():
    from engines.storage.manager import import_project

    data = request.get_json(silent=True) or {}
    archive = data.get("archive") or data.get("path")
    if not archive:
        return jsonify({"ok": False, "error": "archive_required"}), 400
    title = str(data.get("title") or "")
    try:
        record = import_project(archive, title=title, app_dir=APP_DIR)
        return jsonify({"ok": True, "project": record.to_dict()})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "file_not_found"}), 404

