"""
VideoMonster V2 — License API
Demo / Basic / Premium with offline validation.
"""

from __future__ import annotations

import os

from flask import Blueprint, request, jsonify

from engines.license_manager import (
    activate_key,
    deactivate,
    extend_license,
    generate_key,
    get_status,
    revoke_key,
    try_sync,
)

bp = Blueprint("license_api", __name__)


def _owner_ok() -> bool:
    token = os.getenv("VM_OWNER_TOKEN", "").strip()
    if not token:
        token = "vm-owner-local"
    header = request.headers.get("X-VM-Owner-Token", "")
    body = (request.get_json(silent=True) or {}).get("owner_token", "")
    return token and (header == token or body == token)


@bp.get("/api/license/status")
def api_license_status():
    return jsonify(get_status())


@bp.post("/api/license/activate")
def api_license_activate():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()

    if not key:
        return jsonify({"error": "Ключ не указан"}), 400

    ok, _data, msg = activate_key(key)
    if not ok:
        return jsonify({"error": msg}), 400

    status = get_status()
    return jsonify(
        {
            "ok": True,
            "message": msg,
            **status,
        }
    )


@bp.post("/api/license/deactivate")
def api_license_deactivate():
    deactivate()
    return jsonify({"ok": True, **get_status()})


@bp.post("/api/license/sync")
def api_license_sync():
    result = try_sync()
    return jsonify({**result, **get_status()})


@bp.post("/api/license/admin/extend")
def api_license_admin_extend():
    """Owner: extend license 7 / 30 / lifetime days."""
    if not _owner_ok():
        return jsonify({"error": "Требуется токен владельца"}), 403

    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "").lower()

    if mode == "lifetime":
        ok, msg = extend_license(None)
    elif mode in ("7", "30"):
        ok, msg = extend_license(int(mode))
    else:
        days = body.get("days")
        if days is None:
            return jsonify({"error": "Укажите mode: 7, 30, lifetime или days"}), 400
        ok, msg = extend_license(int(days) if days else None)

    return jsonify({"ok": ok, "message": msg, **get_status()})


@bp.post("/api/license/admin/revoke")
def api_license_admin_revoke():
    if not _owner_ok():
        return jsonify({"error": "Требуется токен владельца"}), 403

    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    if not key:
        return jsonify({"error": "key required"}), 400

    revoke_key(key)
    return jsonify({"ok": True, "message": f"Ключ {key} отключён"})


@bp.post("/api/license/admin/generate")
def api_license_admin_generate():
    """Owner: generate a new key (TEST-7, TEST-30, PREMIUM-WEEK, etc.)."""
    if not _owner_ok():
        return jsonify({"error": "Требуется токен владельца"}), 403

    body = request.get_json(silent=True) or {}
    key_type = body.get("type") or "TEST-7"
    try:
        key = generate_key(key_type)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"ok": True, "key": key, "type": key_type})
