"""
TubeDub — API владельца: тестовые сборки и инициализация.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from engines.owner_first_run import (
    is_initialized,
    is_owner_host,
    run_init,
    run_if_needed,
)
from engines.test_build_manager import (
    create_test_build,
    create_test_exe_build,
    extend_test_license,
    get_build_zip_path,
    list_test_builds,
    revoke_test_license,
)
from engines.owner_build_jobs import (
    get_job,
    start_ecosystem_build,
    start_setup_installer_build,
    start_test_exe_build,
)
from engines.installer_builder import get_setup_path
from engines.ecosystem_installer import get_install_dir

bp = Blueprint("owner_api", __name__)

VALID_KEY_TYPES = [
    "TEST-7",
    "TEST-30",
    "PREMIUM-WEEK",
    "PREMIUM-MONTH",
    "PREMIUM-YEAR",
    "LIFETIME",
]


def _owner_ok() -> bool:
    token = os.getenv("VM_OWNER_TOKEN", "").strip()
    if not token:
        token = "vm-owner-local"
    header = request.headers.get("X-VM-Owner-Token", "")
    body = (request.get_json(silent=True) or {}).get("owner_token", "")
    return token and (header == token or body == token)


def _require_owner():
    if not is_owner_host():
        return jsonify({"error": "Доступно только на копии владельца"}), 403
    if not _owner_ok():
        return jsonify({"error": "Требуется токен владельца (X-VM-Owner-Token)"}), 403
    return None


@bp.get("/api/owner/status")
def api_owner_status():
    dev = os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on")
    return jsonify(
        {
            "owner_host": is_owner_host(),
            "initialized": is_initialized(),
            "dev_mode": dev,
            "builds_count": len(list_test_builds()),
            "builds_dir": str(Path(__file__).resolve().parent.parent / "output" / "test_builds"),
        }
    )


@bp.post("/api/owner/first-run")
def api_owner_first_run():
    err = _require_owner()
    if err:
        return err
    force = (request.get_json(silent=True) or {}).get("force", False)
    ok, msg = run_init(force=force)
    return jsonify({"ok": ok, "message": msg, "initialized": is_initialized()})


@bp.get("/api/owner/test-builds")
def api_owner_list_builds():
    if not is_owner_host():
        return jsonify({"error": "Доступно только на копии владельца"}), 403
    if not _owner_ok():
        return jsonify({"error": "Требуется токен владельца"}), 403
    return jsonify({"ok": True, "builds": list_test_builds(), "types": VALID_KEY_TYPES})


@bp.post("/api/owner/test-builds/create")
def api_owner_create_build():
    err = _require_owner()
    if err:
        return err

    body = request.get_json(silent=True) or {}
    key_type = (body.get("type") or body.get("key_type") or "TEST-7").upper()
    label = (body.get("label") or "").strip()

    result = create_test_build(key_type, label=label)
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "Ошибка создания")}), 400
    return jsonify(result)


@bp.post("/api/owner/test-builds/revoke")
def api_owner_revoke_build():
    err = _require_owner()
    if err:
        return err

    key = (request.get_json(silent=True) or {}).get("key", "").strip()
    if not key:
        return jsonify({"error": "key required"}), 400

    ok, msg = revoke_test_license(key)
    return jsonify({"ok": ok, "message": msg})


@bp.post("/api/owner/test-builds/extend")
def api_owner_extend_build():
    err = _require_owner()
    if err:
        return err

    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    if not key:
        return jsonify({"error": "key required"}), 400

    lifetime = body.get("lifetime") or body.get("mode") == "lifetime"
    days = body.get("days")
    if lifetime:
        ok, msg = extend_test_license(key, lifetime=True)
    elif days is not None:
        ok, msg = extend_test_license(key, days=int(days))
    elif body.get("mode") in ("7", "30"):
        ok, msg = extend_test_license(key, days=int(body["mode"]))
    else:
        return jsonify({"error": "Укажите days, mode (7/30/lifetime) или lifetime: true"}), 400

    return jsonify({"ok": ok, "message": msg})


@bp.get("/api/owner/test-builds/download/<build_id>")
def api_owner_download_build(build_id: str):
    if not is_owner_host():
        return jsonify({"error": "Доступно только на копии владельца"}), 403
    if not _owner_ok():
        return jsonify({"error": "Требуется токен владельца"}), 403

    setup_path = get_setup_path(build_id)
    if setup_path:
        return send_file(
            setup_path,
            as_attachment=True,
            download_name=setup_path.name,
            mimetype="application/octet-stream",
        )

    zip_path = get_build_zip_path(build_id)
    if not zip_path:
        return jsonify({"error": "Сборка не найдена"}), 404

    return send_file(
        zip_path,
        as_attachment=True,
        download_name=zip_path.name,
        mimetype="application/zip",
    )


@bp.post("/api/owner/build_installer")
def api_owner_build_installer():
    """Создать TubeDub_Setup.exe (розничный установщик)."""
    err = _require_owner()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "TubeDub Setup").strip()
    job_id = start_setup_installer_build(test_build=False, label=label)
    return jsonify(
        {
            "ok": True,
            "job_id": job_id,
            "status": "running",
            "message": "Сборка TubeDub_Setup.exe запущена (PyInstaller + Inno Setup)",
        }
    )


@bp.get("/api/owner/build_installer/status/<job_id>")
def api_owner_build_installer_status(job_id: str):
    if not is_owner_host():
        return jsonify({"error": "Доступно только на копии владельца"}), 403
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Задача не найдена"}), 404
    return jsonify({"ok": True, **job})


@bp.post("/api/owner/build_test_installer")
def api_owner_build_test_installer():
    """Создать TubeDub_Test_7_Days_Setup.exe."""
    err = _require_owner()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    key_type = (body.get("type") or body.get("key_type") or "TEST-7").upper()
    label = (body.get("label") or "TubeDub Test 7 Days").strip()
    job_id = start_setup_installer_build(test_build=True, key_type=key_type, label=label)
    return jsonify(
        {
            "ok": True,
            "job_id": job_id,
            "status": "running",
            "message": "Сборка TubeDub_Test_7_Days_Setup.exe запущена",
        }
    )


@bp.get("/api/owner/build_test_installer/status/<job_id>")
def api_owner_build_test_installer_status(job_id: str):
    if not is_owner_host():
        return jsonify({"error": "Доступно только на копии владельца"}), 403
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Задача не найдена"}), 404
    return jsonify({"ok": True, **job})


@bp.post("/api/owner/build_ecosystem")
def api_owner_build_ecosystem():
    err = _require_owner()
    if err:
        return err
    job_id = start_ecosystem_build()
    return jsonify(
        {
            "ok": True,
            "job_id": job_id,
            "status": "running",
            "install_dir": str(get_install_dir()),
            "message": "Сборка экосистемы запущена в фоне",
        }
    )


@bp.get("/api/owner/build_ecosystem/status/<job_id>")
def api_owner_build_ecosystem_status(job_id: str):
    if not is_owner_host():
        return jsonify({"error": "Доступно только на копии владельца"}), 403
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Задача не найдена"}), 404
    return jsonify({"ok": True, **job})


@bp.post("/api/owner/build_test_version")
def api_owner_build_test_version():
    err = _require_owner()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    key_type = (body.get("type") or body.get("key_type") or "TEST-7").upper()
    label = (body.get("label") or "TubeDub Test EXE").strip()
    sync = bool(body.get("sync", False))

    if sync:
        result = create_test_exe_build(key_type, label=label)
        if not result.get("ok"):
            return jsonify({"error": result.get("error", "Ошибка")}), 400
        return jsonify(result)

    job_id = start_test_exe_build(key_type, label=label)
    return jsonify(
        {
            "ok": True,
            "job_id": job_id,
            "status": "running",
            "message": "Сборка TubeDub_Test_7_Days.exe запущена",
        }
    )


@bp.get("/api/owner/build_test_version/status/<job_id>")
def api_owner_build_test_version_status(job_id: str):
    if not is_owner_host():
        return jsonify({"error": "Доступно только на копии владельца"}), 403
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Задача не найдена"}), 404
    return jsonify({"ok": True, **job})
