"""Plugin System API — management, local marketplace + optional remote storefront (TZ #9)."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("plugins_api", __name__)


def _dev_mode() -> bool:
    from engines.module_registry.registry import is_developer_session

    return is_developer_session(request_headers=dict(request.headers))


@bp.get("/api/plugins")
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


@bp.get("/api/plugins/capabilities")
def api_plugins_capabilities():
    try:
        from core.plugin_manager import get_plugin_manager

        return jsonify({"ok": True, "capabilities": get_plugin_manager(app_dir=APP_DIR).get_capabilities()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/plugins/registrations")
def api_plugins_registrations():
    """List SDK capability registrations (tts/stt/translation/…)."""
    category = str(request.args.get("category") or "")
    try:
        from core.plugin_manager import get_plugin_manager

        mgr = get_plugin_manager(app_dir=APP_DIR)
        return jsonify({"ok": True, "registrations": mgr.list_registrations(category)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/plugins/invoke")
def api_plugins_invoke():
    """Invoke a registered capability handler.

    Body: {category, name, args?: [], kwargs?: {}}
    """
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    category = str(body.get("category") or "").strip()
    name = str(body.get("name") or "").strip()
    if not category or not name:
        return jsonify({"ok": False, "error": "category and name required"}), 400
    args = body.get("args") or []
    kwargs = body.get("kwargs") or {}
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        return jsonify({"ok": False, "error": "args must be list, kwargs must be object"}), 400
    try:
        from core.plugin_manager import get_plugin_manager

        mgr = get_plugin_manager(app_dir=APP_DIR)
        result = mgr.invoke(category, name, *args, **kwargs)
        return jsonify({"ok": True, "category": category, "name": name, "result": result})
    except KeyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/plugins/marketplace/catalog")
def api_plugins_marketplace_catalog():
    try:
        from core.plugin_manager import get_plugin_manager

        return jsonify(get_plugin_manager(app_dir=APP_DIR).marketplace.catalog())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/plugins/marketplace/remote")
def api_plugins_marketplace_remote():
    """Remote storefront status + catalog (hard-gates when URL env unset)."""
    try:
        from core.plugin_manager import get_plugin_manager

        mp = get_plugin_manager(app_dir=APP_DIR).marketplace
        status = mp.remote_status()
        code = 200 if status.get("configured") else 200
        # Always 200 with honest configured=false — clients must not assume remote exists
        return jsonify({"ok": True, "remote": status}), code
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
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.plugin_manager import get_plugin_manager

        ok = get_plugin_manager(app_dir=APP_DIR).enable(name)
        return jsonify({"ok": ok, "plugin": name})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/plugins/<name>/disable")
def api_plugin_disable(name: str):
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
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
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
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
    """Marketplace: install | update | remove | enable | disable | catalog | remote (§9)."""
    if action == "catalog":
        return api_plugins_marketplace_catalog()
    if action == "remote":
        return api_plugins_marketplace_remote()
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    try:
        from core.plugin_manager import get_plugin_manager

        mp = get_plugin_manager(app_dir=APP_DIR).marketplace
        if action == "install":
            # Remote: {remote:true, id} | {url} | http(s) source — hard-gated in marketplace
            if body.get("remote") or body.get("plugin_id") or body.get("id"):
                result = mp.install_remote(
                    str(body.get("plugin_id") or body.get("id") or body.get("name") or ""),
                    name=str(body.get("name") or ""),
                )
            elif body.get("url"):
                result = mp.install_from_url(str(body.get("url")), name=str(body.get("name") or ""))
            else:
                result = mp.install(body.get("source", ""), name=str(body.get("name") or ""))
        elif action == "install_remote":
            result = mp.install_remote(
                str(body.get("plugin_id") or body.get("id") or body.get("name") or ""),
                name=str(body.get("name") or ""),
            )
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
        status = 200 if result.get("ok") else (
            503 if result.get("error") == "remote_marketplace_not_configured" else 400
        )
        return jsonify(result), status
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500