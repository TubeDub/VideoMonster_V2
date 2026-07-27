"""HTTP surface for Platform SDK (Part 8) — does not modify Core engines."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

bp = Blueprint("platform_sdk_api", __name__, url_prefix="/api/platform_sdk")


@bp.get("/status")
def api_status():
    from engines.platform_sdk import bootstrap_platform, platform_status

    # Soft bootstrap on first status call
    try:
        bootstrap_platform(discover_builtin=True)
    except Exception:
        pass
    return jsonify(platform_status())


@bp.get("/extension-points")
def api_extension_points():
    from engines.platform_sdk import get_public_api

    return jsonify({"points": get_public_api().list_extension_points()})


@bp.get("/profiles")
def api_profiles():
    from engines.platform_sdk.settings_profiles import list_profiles

    return jsonify({"profiles": list_profiles()})


@bp.post("/webhooks")
def api_register_webhook():
    from engines.platform_sdk.webhooks import get_webhook_registry

    data = request.get_json(silent=True) or {}
    row = get_webhook_registry().register(
        str(data.get("url") or ""),
        events=list(data.get("events") or []),
        secret=str(data.get("secret") or ""),
        name=str(data.get("name") or ""),
    )
    return jsonify(row)


@bp.get("/marketplace")
def api_marketplace():
    """Local .vmplugin catalog + read-only plugins curated catalog cross-ref.

    Does not mutate plugin manager state — plugins sibling owns install/enable.
    """
    from engines.platform_sdk.marketplace import get_marketplace

    kind = request.args.get("kind")
    market = get_marketplace()
    items = market.list_items(kind=kind)
    plugins_catalog = market.plugins_catalog_snapshot()
    return jsonify(
        {
            "ok": True,
            "store": "local",
            "items": items,
            "kinds": market.kinds(),
            "count": len(items),
            "plugins_catalog": plugins_catalog,
            "hint": "Plugin install/enable lives at /api/plugins/marketplace/*",
        }
    )


@bp.get("/marketplace/kinds")
def api_marketplace_kinds():
    from engines.platform_sdk.marketplace import get_marketplace

    return jsonify({"ok": True, "kinds": get_marketplace().kinds()})


@bp.post("/marketplace/publish")
def api_marketplace_publish():
    """Publish a descriptor into the local Platform SDK marketplace catalog."""
    from engines.platform_sdk.marketplace import get_marketplace
    from engines.platform_sdk.types import MarketplaceKind, PluginDescriptor

    data = request.get_json(silent=True) or {}
    plugin_id = str(data.get("plugin_id") or data.get("id") or "").strip()
    if not plugin_id:
        return jsonify({"ok": False, "error": "plugin_id required"}), 400
    version = str(data.get("version") or "1.0.0")
    kind_raw = str(data.get("kind") or MarketplaceKind.AUTOMATION.value)
    try:
        kind = MarketplaceKind(kind_raw)
    except ValueError:
        kind = MarketplaceKind.AUTOMATION
    desc = PluginDescriptor(
        plugin_id=plugin_id,
        version=version,
        permissions=list(data.get("permissions") or []),
        description=str(data.get("description") or ""),
        author=str(data.get("author") or ""),
    )
    entry = get_marketplace().publish(
        desc,
        kind=kind,
        package_path=data.get("package_path"),
        secret=data.get("secret"),
    )
    return jsonify({"ok": True, "entry": entry})


@bp.get("/cloud/status")
def api_cloud_status():
    """Honest local cloud façade status (no remote OAuth required)."""
    from engines.platform_sdk.cloud import get_cloud_facade

    cloud = get_cloud_facade()
    projects = list(cloud.projects_dir.glob("*.json")) if cloud.projects_dir.is_dir() else []
    return jsonify(
        {
            "ok": True,
            "mode": "local_mirror",
            "root": str(cloud.root),
            "projects_count": len(projects),
            "remote_oauth": "use /api/cloud/oauth/* for provider tokens",
        }
    )


@bp.get("/cloud/projects/<project_id>")
def api_cloud_open_project(project_id: str):
    from engines.platform_sdk.cloud import get_cloud_facade

    data = get_cloud_facade().open_project(project_id)
    if data is None:
        return jsonify({"ok": False, "error": "not_found", "mode": "local_mirror"}), 404
    return jsonify({"ok": True, "project": data, "mode": "local_mirror"})


@bp.post("/cloud/projects/<project_id>")
def api_cloud_save_project(project_id: str):
    from engines.platform_sdk.cloud import get_cloud_facade

    payload = request.get_json(silent=True) or {}
    path = get_cloud_facade().save_project(project_id, payload if isinstance(payload, dict) else {})
    return jsonify({"ok": True, "path": str(path), "mode": "local_mirror"})


@bp.post("/cloud/projects/<project_id>/sync")
def api_cloud_sync_project(project_id: str):
    from engines.platform_sdk.cloud import get_cloud_facade

    return jsonify(get_cloud_facade().sync_project(project_id))


@bp.post("/cloud/projects/<project_id>/backup")
def api_cloud_backup_project(project_id: str):
    from engines.platform_sdk.cloud import get_cloud_facade

    try:
        path = get_cloud_facade().backup_project(project_id)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "not_found", "mode": "local_mirror"}), 404
    return jsonify({"ok": True, "path": str(path), "mode": "local_mirror"})


@bp.get("/cloud/projects/<project_id>/backups")
def api_cloud_list_backups(project_id: str):
    from engines.platform_sdk.cloud import get_cloud_facade

    return jsonify(
        {
            "ok": True,
            "backups": get_cloud_facade().list_backups(project_id),
            "mode": "local_mirror",
        }
    )
