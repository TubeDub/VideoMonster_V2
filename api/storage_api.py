"""Storage audit & safe temp cleanup API (TubeDub Storage TZ §8–§10)."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger("tubedub.api.storage")

bp = Blueprint("storage_api", __name__)

APP_DIR = Path(__file__).resolve().parents[1]


def _deny_non_local(*, action: str):
    from engines.request_guards import require_local_mutating

    return require_local_mutating(request, action=action)


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
    Header: X-VM-Destructive-Confirm: 1
    """
    data = request.get_json(silent=True) or {}
    from engines.request_guards import require_destructive_confirm

    denied = require_destructive_confirm(request, data)
    if denied is not None:
        return denied

    scope = str(data.get("scope") or "all").strip().lower()
    from engines.storage_cleanup import (
        StorageCleanupReport,
        cleanup_all_temp_and_cache,
        cleanup_llm_rewrite_cache,
        cleanup_pipeline_cache,
        cleanup_pipeline_temp,
        cleanup_stale_imports,
        cleanup_stale_loose_uploads,
        cleanup_stale_translate_uploads,
    )

    report: StorageCleanupReport
    if scope == "temp":
        report = cleanup_pipeline_temp(APP_DIR)
    elif scope in ("pipeline_cache", "cache"):
        report = cleanup_pipeline_cache(APP_DIR)
    elif scope in ("llm_cache", "llm"):
        report = cleanup_llm_rewrite_cache(APP_DIR)
    elif scope in ("imports", "stale_imports"):
        report = cleanup_stale_imports(APP_DIR)
    elif scope in ("translate_uploads", "stt_temps"):
        report = cleanup_stale_translate_uploads(APP_DIR)
    elif scope in ("uploads", "loose_uploads"):
        report = cleanup_stale_loose_uploads(APP_DIR)
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
    denied = _deny_non_local(action="create")
    if denied is not None:
        return denied

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
    denied = _deny_non_local(action="open")
    if denied is not None:
        return denied

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
    denied = _deny_non_local(action="save")
    if denied is not None:
        return denied

    from engines.storage.locks import StorageLockError
    from engines.storage.manager import save_project

    data = request.get_json(silent=True) or {}
    try:
        record = save_project(project_id, data, app_dir=APP_DIR)
        return jsonify({"ok": True, "project": record.to_dict()})
    except StorageLockError as exc:
        return jsonify({"ok": False, "error": "locked", "holder": getattr(exc, "holder", "")}), 423
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "not_found"}), 404


@bp.post("/api/storage/projects/<project_id>/close")
def api_storage_close_project(project_id: str):
    denied = _deny_non_local(action="close")
    if denied is not None:
        return denied

    from engines.storage.manager import close_project

    close_project(project_id, app_dir=APP_DIR)
    return jsonify({"ok": True})


@bp.post("/api/storage/projects/<project_id>/trash")
def api_storage_trash_project(project_id: str):
    from engines.request_guards import require_destructive_confirm
    from engines.storage.manager import move_to_trash

    data = request.get_json(silent=True) or {}
    denied = require_destructive_confirm(request, data)
    if denied is not None:
        return denied

    ok = move_to_trash(project_id, app_dir=APP_DIR)
    return jsonify({"ok": ok})


@bp.post("/api/storage/projects/<project_id>/restore")
def api_storage_restore_project(project_id: str):
    denied = _deny_non_local(action="restore")
    if denied is not None:
        return denied

    from engines.storage.manager import restore_project

    try:
        record = restore_project(project_id, app_dir=APP_DIR)
        return jsonify({"ok": True, "project": record.to_dict()})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "not_in_trash"}), 404


@bp.delete("/api/storage/projects/<project_id>")
def api_storage_delete_project(project_id: str):
    from engines.request_guards import require_destructive_confirm
    from engines.storage.manager import delete_project

    data = request.get_json(silent=True) or {}
    denied = require_destructive_confirm(request, data)
    if denied is not None:
        return denied

    permanent = request.args.get("permanent", "").lower() in ("1", "true", "yes")
    ok = delete_project(project_id, permanent=permanent, app_dir=APP_DIR)
    return jsonify({"ok": ok})


@bp.post("/api/storage/trash/empty")
def api_storage_empty_trash():
    from engines.request_guards import require_destructive_confirm
    from engines.storage.manager import empty_trash

    data = request.get_json(silent=True) or {}
    denied = require_destructive_confirm(request, data)
    if denied is not None:
        return denied

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

    try:
        recovery = check_session_recovery(APP_DIR)
        return jsonify({"ok": True, "recovery": recovery})
    except Exception as exc:  # noqa: BLE001
        logger.exception("storage recovery check failed")
        return jsonify({"ok": False, "error": str(exc), "recovery": None}), 500


@bp.get("/api/storage/statistics")
def api_storage_statistics():
    from engines.storage.manager import get_statistics

    return jsonify({"ok": True, "statistics": get_statistics(APP_DIR)})


@bp.post("/api/storage/projects/<project_id>/export")
def api_storage_export_project(project_id: str):
    denied = _deny_non_local(action="export")
    if denied is not None:
        return denied

    from engines.path_safety import clamp_write_path, safe_filename
    from engines.storage.manager import export_project

    data = request.get_json(silent=True) or {}
    default_name = f"{safe_filename(project_id, default='project')}.vmproj.zip"
    try:
        dest_path = clamp_write_path(
            data.get("dest") or default_name,
            APP_DIR / "output",
            default_name=default_name,
        )
    except ValueError:
        return jsonify({"ok": False, "error": "invalid_dest"}), 400
    try:
        path = export_project(project_id, dest_path, app_dir=APP_DIR)
        return jsonify({"ok": True, "path": str(path)})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "not_found"}), 404


@bp.post("/api/storage/projects/import")
def api_storage_import_project():
    denied = _deny_non_local(action="import")
    if denied is not None:
        return denied

    from engines.path_safety import resolve_under_roots
    from engines.storage.manager import import_project

    data = request.get_json(silent=True) or {}
    archive = data.get("archive") or data.get("path")
    if not archive:
        return jsonify({"ok": False, "error": "archive_required"}), 400
    archive_path = resolve_under_roots(
        archive,
        [APP_DIR / "uploads", APP_DIR / "uploads" / "imports", APP_DIR / "output"],
        basename_fallback=True,
    )
    if archive_path is None:
        return jsonify({"ok": False, "error": "file_not_found"}), 404
    title = str(data.get("title") or "")
    try:
        record = import_project(archive_path, title=title, app_dir=APP_DIR)
        return jsonify({"ok": True, "project": record.to_dict()})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "file_not_found"}), 404
