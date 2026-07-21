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
    from engines.platform_sdk.marketplace import get_marketplace

    kind = request.args.get("kind")
    return jsonify({"items": get_marketplace().list_items(kind=kind)})
