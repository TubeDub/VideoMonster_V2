"""Feature Flags API — Developer Panel, toggles, log."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("feature_flags_api", __name__)


def _dev_mode() -> bool:
    from engines.module_registry.registry import is_developer_session

    return is_developer_session(request_headers=dict(request.headers))


def _user_mode() -> str:
    from engines.feature_flags.modes import normalize_mode

    raw = (
        request.args.get("mode")
        or request.headers.get("X-VM-User-Mode")
        or request.headers.get("X-VM-UI-Mode")
        or "basic"
    )
    return normalize_mode(raw)


@bp.get("/api/features/status")
def api_features_status():
    from engines.feature_flags.manager import get_feature_manager

    fm = get_feature_manager(APP_DIR)
    dev = _dev_mode()
    return jsonify(
        {
            "developer_session": dev,
            "user_mode": _user_mode(),
            "bootstrap": fm.bootstrap() if dev else {"ok": True, "skipped": True},
        }
    )


@bp.get("/api/features/panel")
def api_features_panel():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer session required"}), 403
    from engines.module_registry.registry import get_registry
    from engines.feature_flags.dev_log import get_dev_log
    from engines.feature_flags.manager import get_feature_manager

    fm = get_feature_manager(APP_DIR)
    reg = get_registry(APP_DIR)
    snap = fm.panel_snapshot(
        user_mode="developer",
        developer_session=True,
        show_beta=reg.show_beta_to_users(),
    )
    snap["ok"] = True
    snap["dev_log"] = get_dev_log(APP_DIR).snapshot(80)
    return jsonify(snap)


@bp.get("/api/features/log")
def api_features_log():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer session required"}), 403
    from engines.feature_flags.dev_log import get_dev_log

    limit = min(int(request.args.get("limit") or 100), 500)
    return jsonify({"ok": True, "events": get_dev_log(APP_DIR).snapshot(limit)})


@bp.patch("/api/features/<feature_id>")
def api_features_patch(feature_id: str):
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer session required"}), 403
    from engines.feature_flags.manager import VALID_STATUSES, get_feature_manager

    data = request.get_json(silent=True) or {}
    fm = get_feature_manager(APP_DIR)
    rec = fm.get(feature_id)
    if not rec:
        return jsonify({"ok": False, "error": "Feature not found"}), 404

    action = str(data.get("action") or "").strip().lower()
    if action in ("on", "enable"):
        rec = fm.set_enabled(feature_id, True)
    elif action in ("off", "disable"):
        rec = fm.set_enabled(feature_id, False)
    elif action == "ready":
        rec = fm.set_status(feature_id, "READY")
        fm.set_enabled(feature_id, True)
    elif action == "beta":
        rec = fm.set_status(feature_id, "BETA")
    elif action == "experimental":
        rec = fm.set_status(feature_id, "EXPERIMENTAL")
    elif action == "development":
        rec = fm.set_status(feature_id, "DEVELOPMENT")
    else:
        if "enabled" in data:
            rec = fm.set_enabled(feature_id, bool(data["enabled"]))
        if "status" in data:
            st = str(data["status"]).upper()
            if st not in {s.upper() for s in VALID_STATUSES}:
                return jsonify({"ok": False, "error": "Invalid status"}), 400
            rec = fm.set_status(feature_id, st)

    if not rec:
        return jsonify({"ok": False, "error": "Update failed"}), 400
    return jsonify({"ok": True, "feature": rec.to_dict()})


@bp.get("/api/features/check/<feature_id>")
def api_features_check(feature_id: str):
    from engines.module_registry.registry import get_registry, is_developer_session
    from engines.feature_flags.manager import get_feature_manager

    fm = get_feature_manager(APP_DIR)
    reg = get_registry(APP_DIR)
    dev = is_developer_session(request_headers=dict(request.headers))
    mode = _user_mode()
    return jsonify(
        {
            "feature_id": feature_id,
            "enabled": fm.is_enabled(
                feature_id,
                user_mode=mode,
                developer_session=dev,
                show_beta=reg.show_beta_to_users(),
            ),
            "visible": fm.is_visible_in_nav(
                feature_id,
                user_mode=mode,
                developer_session=dev,
                show_beta=reg.show_beta_to_users(),
            ),
            "user_mode": mode,
            "developer_session": dev,
        }
    )
