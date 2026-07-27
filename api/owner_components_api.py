"""Owner AI Download Center API."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("owner_components_api", __name__)


def _owner_ok() -> bool:
    from engines.owner_first_run import is_owner_host

    if not is_owner_host():
        return False
    addr = (request.remote_addr or "").strip().lower()
    if addr not in ("127.0.0.1", "::1", "localhost"):
        return False
    token = os.getenv("VM_OWNER_TOKEN", "").strip() or "vm-owner-local"
    header = request.headers.get("X-VM-Owner-Token", "")
    body = (request.get_json(silent=True) or {}).get("owner_token", "")
    return header == token or body == token


def _guard():
    if not _owner_ok():
        return jsonify({"error": "Требуется доступ владельца"}), 403
    return None


@bp.get("/api/owner/components")
def api_owner_components():
    if err := _guard():
        return err
    from engines.model_manager import get_storage_status, list_components

    return jsonify({"components": list_components(APP_DIR), "storage": get_storage_status(APP_DIR)})


@bp.get("/api/owner/storage/status")
def api_owner_storage():
    if err := _guard():
        return err
    from engines.model_manager import get_storage_status

    return jsonify(get_storage_status(APP_DIR))


@bp.get("/api/owner/storage/drives")
def api_owner_drives():
    if err := _guard():
        return err
    from engines.model_manager import list_drives

    return jsonify({"drives": list_drives()})


@bp.post("/api/owner/storage/limit")
def api_owner_limit():
    if err := _guard():
        return err
    from engines.model_manager import set_max_storage_gb

    gb = float((request.get_json(silent=True) or {}).get("max_storage_gb", 10))
    set_max_storage_gb(APP_DIR, gb)
    return jsonify({"ok": True, "max_storage_gb": gb})


@bp.post("/api/owner/storage/root")
def api_owner_storage_root():
    if err := _guard():
        return err
    from engines.model_manager import set_storage_root

    path = str((request.get_json(silent=True) or {}).get("path", "")).strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    result = set_storage_root(APP_DIR, Path(path))
    return jsonify(result)


@bp.post("/api/owner/components/delete")
def api_owner_delete():
    if err := _guard():
        return err
    from engines.model_manager import delete_component

    data = request.get_json(silent=True) or {}
    confirmed = bool(data.get("confirmed", False))
    r = delete_component(APP_DIR, data.get("component_id", ""), data.get("variant", ""), force=confirmed)
    status = 200 if r.get("ok") else 400
    return jsonify(r), status


@bp.post("/api/owner/components/update")
def api_owner_update():
    if err := _guard():
        return err
    from engines.model_manager import update_component
    from engines.model_manager.runtime import prepare_download_session

    data = request.get_json(silent=True) or {}
    with prepare_download_session():
        r = update_component(APP_DIR, data.get("component_id", ""), data.get("variant", ""))
    return jsonify(r)


@bp.get("/api/owner/cleanup/suggestions")
def api_owner_suggestions():
    if err := _guard():
        return err
    from engines.model_manager import suggest_cleanup

    return jsonify({"suggestions": suggest_cleanup(APP_DIR)})


@bp.post("/api/owner/cleanup/apply")
def api_owner_cleanup_apply():
    if err := _guard():
        return err
    from engines.model_manager import apply_cleanup, apply_lru_if_allowed

    data = request.get_json(silent=True) or {}
    confirmed = bool(data.get("confirmed", False))
    if data.get("lru"):
        return jsonify(apply_lru_if_allowed(APP_DIR, confirmed=confirmed))
    keys = data.get("keys") or []
    return jsonify(apply_cleanup(APP_DIR, keys, confirmed=confirmed))


def _open_allowed_dir(path: str | Path) -> tuple[Path | None, str]:
    """Open only directories under app dir or configured model storage root."""
    from engines.model_manager.storage import get_storage_root
    from engines.path_safety import is_under_root

    raw = Path(str(path or "").strip() or get_storage_root(APP_DIR))
    try:
        resolved = raw.resolve()
    except OSError:
        return None, "invalid_path"
    if not resolved.is_dir():
        return None, "not_found"
    storage_root = Path(get_storage_root(APP_DIR)).resolve()
    if not (is_under_root(resolved, APP_DIR) or is_under_root(resolved, storage_root)):
        return None, "path_forbidden"
    return resolved, ""


def _shell_open_dir(path: Path) -> None:
    if os.name == "nt":
        subprocess.Popen(["explorer", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


@bp.post("/api/owner/components/open-folder")
def api_owner_open_component_folder():
    if err := _guard():
        return err

    path = str((request.get_json(silent=True) or {}).get("path", "")).strip()
    resolved, err_code = _open_allowed_dir(path)
    if not resolved:
        return jsonify({"ok": False, "error": err_code}), 404 if err_code == "not_found" else 403
    _shell_open_dir(resolved)
    return jsonify({"ok": True, "path": str(resolved)})


@bp.post("/api/owner/storage/open-folder")
def api_owner_open_folder():
    if err := _guard():
        return err
    from engines.model_manager.storage import get_storage_root

    path = str((request.get_json(silent=True) or {}).get("path") or get_storage_root(APP_DIR))
    resolved, err_code = _open_allowed_dir(path)
    if not resolved:
        return jsonify({"ok": False, "error": err_code}), 404 if err_code == "not_found" else 403
    _shell_open_dir(resolved)
    return jsonify({"ok": True, "path": str(resolved)})
