"""TubeDub Platform API — architecture layer (developer + public bus)."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("tubedub_platform_api", __name__)


def _dev_mode() -> bool:
    from engines.module_registry.registry import is_developer_session

    return is_developer_session(request_headers=dict(request.headers))


@bp.get("/api/tubedub/platform/status")
def api_tubedub_status():
    from engines.tubedub.bootstrap import bootstrap_platform

    result = bootstrap_platform(APP_DIR)
    return jsonify({"ok": True, **result})


@bp.get("/api/tubedub/platform/modules")
def api_tubedub_modules():
    from engines.tubedub.module_manager import get_module_manager

    mgr = get_module_manager(APP_DIR)
    if not mgr.all_modules():
        mgr.bootstrap()
    return jsonify(
        {
            "ok": True,
            **mgr.snapshot(
                developer_session=_dev_mode(),
                user_mode="developer" if _dev_mode() else "basic",
            ),
        }
    )


@bp.get("/api/tubedub/platform/architecture")
def api_tubedub_architecture():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    from engines.tubedub.dev_mode.dashboard import build_architecture_dashboard

    task_id = request.args.get("task_id") or ""
    task_info = _load_task_info(task_id) if task_id else None
    dash = build_architecture_dashboard(
        APP_DIR,
        developer_session=True,
        user_mode="developer",
        task_info=task_info,
    )
    return jsonify({"ok": True, "dashboard": dash})


@bp.post("/api/tubedub/platform/bus")
def api_tubedub_bus_call():
    """Public API bus endpoint — all inter-module calls should use this."""
    body = request.get_json(silent=True) or {}
    namespace = str(body.get("namespace") or "")
    method = str(body.get("method") or "")
    if not namespace or not method:
        return jsonify({"ok": False, "error": "namespace and method required"}), 400

    feature_id = _namespace_feature(namespace)
    if feature_id:
        from engines.feature_flags.manager import get_feature_manager

        fm = get_feature_manager(APP_DIR)
        if not fm.is_enabled(
            feature_id,
            developer_session=_dev_mode(),
            user_mode="developer" if _dev_mode() else "basic",
        ):
            return jsonify({"ok": False, "error": f"Module {feature_id} not available"}), 403

    from engines.tubedub.api_bus import get_api_bus
    from engines.tubedub.module_manager import get_module_manager

    mgr = get_module_manager(APP_DIR)
    if not mgr.all_modules():
        mgr.bootstrap()

    resp = get_api_bus().call(
        namespace,
        method,
        dict(body.get("payload") or {}),
        caller=str(body.get("caller") or "http"),
    )
    return jsonify({"ok": resp.ok, **resp.to_dict()})


@bp.get("/api/tubedub/platform/projects")
def api_tdproj_list():
    from engines.tubedub.project.store import get_project_store

    store = get_project_store(APP_DIR)
    return jsonify({"ok": True, "projects": store.list_projects()})


@bp.post("/api/tubedub/platform/projects")
def api_tdproj_create():
    from engines.tubedub.api_bus import get_api_bus
    from engines.tubedub.module_manager import get_module_manager

    mgr = get_module_manager(APP_DIR)
    if not mgr.all_modules():
        mgr.bootstrap()
    body = request.get_json(silent=True) or {}
    resp = get_api_bus().call("project", "create", body, caller="http")
    if not resp.ok:
        return jsonify({"ok": False, "error": resp.error}), 400
    return jsonify({"ok": True, "project": resp.result})


@bp.get("/api/tubedub/platform/projects/<project_id>")
def api_tdproj_load(project_id: str):
    from engines.tubedub.api_bus import get_api_bus
    from engines.tubedub.module_manager import get_module_manager

    mgr = get_module_manager(APP_DIR)
    if not mgr.all_modules():
        mgr.bootstrap()
    resp = get_api_bus().call("project", "load", {"project_id": project_id}, caller="http")
    if not resp.ok:
        return jsonify({"ok": False, "error": resp.error}), 400
    return jsonify({"ok": True, "project": resp.result})


@bp.patch("/api/tubedub/platform/features/<feature_id>/channel")
def api_feature_release_channel(feature_id: str):
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    channel = str(body.get("release_channel") or body.get("channel") or "").upper()
    if channel not in ("DISABLED", "DEVELOPER", "RELEASE"):
        return jsonify({"ok": False, "error": "channel must be DISABLED, DEVELOPER, or RELEASE"}), 400
    from engines.feature_flags.manager import get_feature_manager

    rec = get_feature_manager(APP_DIR).set_release_channel(feature_id, channel)
    if not rec:
        return jsonify({"ok": False, "error": "feature not found"}), 404
    return jsonify({"ok": True, "feature": rec.to_dict()})


def _namespace_feature(namespace: str) -> str | None:
    mapping = {
        "dub_studio": "dub_studio",
        "pipeline": "pipeline_platform",
        "cloud": "cloud_platform",
        "live": "live_translation",
    }
    return mapping.get(namespace)


def _load_task_info(task_id: str) -> dict | None:
    try:
        from api.auto_dub_api import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                return dict(task.get("info") or {})
    except Exception:
        pass
    return None
