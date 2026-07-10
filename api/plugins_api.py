"""Plugin System API — management & marketplace stubs (TZ #9)."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("plugins_api", __name__)


def _dev_mode() -> bool:
    from engines.module_registry.registry import is_developer_session

    return is_developer_session(request_headers=dict(request.headers))


@bp.get("/api/plugins/status")
def api_plugins_status():
    try:
        from core.plugin_manager import get_plugin_manager

        return jsonify({"ok": True, "status": get_plugin_manager(app_dir=APP_DIR).get_status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/plugins/diagnostics")
def api_plugins_diagnostics():
    """Plugin diagnostics for Monitoring Center integration (§14)."""
    try:
        from core.plugin_manager import get_plugin_manager

        mgr = get_plugin_manager(app_dir=APP_DIR)
        return jsonify({"ok": True, "plugins": mgr.get_diagnostics()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/plugins/<name>")
def api_plugin_detail(name: str):
    try:
        from core.plugin_manager import get_plugin_manager

        detail = get_plugin_manager(app_dir=APP_DIR).get_plugin(name)
        if not detail:
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": True, "plugin": detail})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/plugins/<name>/enable")
def api_plugin_enable(name: str):
    try:
        from core.plugin_manager import get_plugin_manager

        ok = get_plugin_manager(app_dir=APP_DIR).enable(name)
        return jsonify({"ok": ok, "plugin": name})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/plugins/<name>/disable")
def api_plugin_disable(name: str):
    try:
        from core.plugin_manager import get_plugin_manager

        ok = get_plugin_manager(app_dir=APP_DIR).disable(name)
        return jsonify({"ok": ok, "plugin": name})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/plugins/<name>/reload")
def api_plugin_reload(name: str):
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.plugin_manager import get_plugin_manager

        ok = get_plugin_manager(app_dir=APP_DIR).reload(name)
        return jsonify({"ok": ok, "plugin": name})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/plugins/<name>/permissions")
def api_plugin_permissions(name: str):
    body = request.get_json(silent=True) or {}
    try:
        from core.plugin_manager import get_plugin_manager

        mgr = get_plugin_manager(app_dir=APP_DIR)
        mgr.set_permissions(name, body)
        if body.get("reload"):
            mgr.reload(name)
        return jsonify({"ok": True, "plugin": name, "permissions": body})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/plugins/marketplace/<action>")
def api_plugins_marketplace(action: str):
    """Marketplace API stub: install | update | remove | enable | disable (§9)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    try:
        from core.plugin_manager import get_plugin_manager

        mp = get_plugin_manager(app_dir=APP_DIR).marketplace
        if action == "install":
            result = mp.install(body.get("source", ""), name=str(body.get("name") or ""))
        elif action == "update":
            result = mp.update(str(body.get("name", "")), body.get("source", ""))
        elif action == "remove":
            result = mp.remove(str(body.get("name", "")))
        elif action == "enable":
            result = mp.enable(str(body.get("name", "")))
        elif action == "disable":
            result = mp.disable(str(body.get("name", "")))
        else:
            return jsonify({"ok": False, "error": "unknown action"}), 400
        return jsonify(result)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/plugins/capabilities")
def api_plugins_capabilities():
    try:
        from core.plugin_manager import get_plugin_manager

        return jsonify({"ok": True, "capabilities": get_plugin_manager(app_dir=APP_DIR).get_capabilities()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500
