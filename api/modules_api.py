"""Module registry API — nav, developer mode, module management."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("modules_api", __name__)


def _dev_mode() -> bool:
    from engines.module_registry.registry import is_developer_session

    return is_developer_session(request_headers=dict(request.headers))


def _lang() -> str:
    return (request.args.get("lang") or request.headers.get("X-VM-UI-Lang") or "ru").split("-")[0]


def _user_mode() -> str:
    from engines.feature_flags.modes import normalize_mode

    raw = (
        request.args.get("mode")
        or request.headers.get("X-VM-User-Mode")
        or request.headers.get("X-VM-UI-Mode")
        or "basic"
    )
    if _dev_mode():
        return "developer"
    return normalize_mode(raw)


@bp.get("/api/modules/status")
def api_modules_status():
    from engines.module_registry.registry import get_registry, is_developer_session

    reg = get_registry(APP_DIR)
    dev = is_developer_session(request_headers=dict(request.headers))
    return jsonify(
        {
            "developer_mode": dev,
            "show_beta_to_users": reg.show_beta_to_users(),
            "registry_path": str(reg._local_path),
        }
    )


@bp.get("/api/modules/nav")
def api_modules_nav():
    from engines.module_registry.registry import get_registry

    reg = get_registry(APP_DIR)
    return jsonify(
        {
            "developer_mode": _dev_mode(),
            "user_mode": _user_mode(),
            "items": reg.nav_modules(
                developer_mode=_dev_mode(),
                lang=_lang(),
                user_mode=_user_mode(),
            ),
        }
    )


@bp.get("/api/modules/registry")
def api_modules_registry():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    from engines.module_registry.registry import get_registry

    reg = get_registry(APP_DIR)
    return jsonify({"ok": True, **reg.snapshot(developer_mode=True, lang=_lang())})


@bp.patch("/api/modules/registry/<module_id>")
def api_modules_patch(module_id: str):
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    from engines.module_registry.registry import VALID_STATUSES, get_registry

    data = request.get_json(silent=True) or {}
    reg = get_registry(APP_DIR)

    action = str(data.get("action") or "").strip().lower()
    patch: dict = {}

    if action == "stable":
        patch = {"status": "stable", "visible_to_users": True}
    elif action == "beta":
        patch = {"status": "beta", "visible_to_users": True}
    elif action == "development":
        patch = {"status": "development", "visible_to_users": False}
    elif action == "disable":
        patch = {"status": "disabled", "visible_to_users": False}
    elif action == "hide_users":
        patch = {"visible_to_users": False}
    elif action == "show_users":
        patch = {"visible_to_users": True}
    else:
        for key in ("status", "visible_to_users", "show_in_menu", "show_experimental_badge", "pro_only"):
            if key in data:
                patch[key] = data[key]
        if patch.get("status") and patch["status"] not in VALID_STATUSES:
            return jsonify({"ok": False, "error": "Invalid status"}), 400

    updated = reg.update_module(module_id, patch)
    if not updated:
        return jsonify({"ok": False, "error": "Module not found"}), 404
    return jsonify(
        {
            "ok": True,
            "module": updated.to_public_dict(lang=_lang(), developer_mode=True),
        }
    )


@bp.post("/api/modules/settings")
def api_modules_settings():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    from engines.module_registry.registry import get_registry

    data = request.get_json(silent=True) or {}
    reg = get_registry(APP_DIR)
    if "show_beta_to_users" in data:
        reg.set_show_beta_to_users(bool(data.get("show_beta_to_users")))
    return jsonify(
        {
            "ok": True,
            "show_beta_to_users": reg.show_beta_to_users(),
        }
    )
