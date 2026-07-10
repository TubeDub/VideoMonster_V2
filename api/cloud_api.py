"""TubeDub Cloud Platform API."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("cloud_api", __name__)


def _guard():
    from engines.cloud.config import require_cloud

    try:
        require_cloud()
    except PermissionError as e:
        return str(e)
    return None


def _svc():
    from engines.cloud.service import get_cloud_service

    return get_cloud_service(APP_DIR)


@bp.get("/api/cloud/status")
def api_cloud_status():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "enabled": False, "error": blocked}), 403
    return jsonify({"ok": True, **_svc().status()})


@bp.get("/api/cloud/providers")
def api_cloud_providers():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    return jsonify({"ok": True, "providers": _svc().manager.list_providers_status()})


@bp.get("/api/cloud/projects")
def api_cloud_projects():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    return jsonify({"ok": True, "projects": _svc().list_projects()})


@bp.get("/api/cloud/projects/<project_id>")
def api_cloud_project_get(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    row = _svc().get_project(project_id)
    if not row:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True, "project": row})


@bp.patch("/api/cloud/projects/<project_id>")
def api_cloud_project_patch(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    svc = _svc()
    if "title" in data:
        project = svc.manager.rename_project(project_id, str(data["title"]))
        return jsonify({"ok": True, "project": project.to_dict()})
    if data.get("action") == "restore_version" and data.get("version_id"):
        project = svc.manager.restore_version(project_id, str(data["version_id"]))
        return jsonify({"ok": True, "project": project.to_dict()})
    return jsonify({"ok": False, "error": "Unknown action"}), 400


@bp.delete("/api/cloud/projects/<project_id>")
def api_cloud_project_delete(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    delete_local = request.args.get("delete_local") in ("1", "true", "yes")
    ok = _svc().manager.delete_project(project_id, delete_local=delete_local)
    return jsonify({"ok": ok})


@bp.get("/api/cloud/files")
def api_cloud_files():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    provider_id = request.args.get("provider") or "local"
    q = request.args.get("q") or ""
    files = _svc().manager.search_files(q, provider_id=provider_id)
    return jsonify({"ok": True, "files": files})


@bp.post("/api/cloud/upload")
def api_cloud_upload():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    local_path = (data.get("local_path") or data.get("filename") or "").strip()
    remote_path = (data.get("remote_path") or local_path).strip()
    provider_id = (data.get("provider_id") or "local").strip()
    if not local_path:
        return jsonify({"ok": False, "error": "local_path required"}), 400
    task = _svc().enqueue_upload(local_path, remote_path, provider_id=provider_id)
    return jsonify({"ok": True, "task": task})


@bp.post("/api/cloud/download")
def api_cloud_download():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    remote_path = (data.get("remote_path") or "").strip()
    local_path = (data.get("local_path") or remote_path.split("/")[-1]).strip()
    provider_id = (data.get("provider_id") or "local").strip()
    if not remote_path:
        return jsonify({"ok": False, "error": "remote_path required"}), 400
    task = _svc().enqueue_download(remote_path, local_path, provider_id=provider_id)
    return jsonify({"ok": True, "task": task})


@bp.get("/api/cloud/sync/queue")
def api_cloud_sync_queue():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    return jsonify({"ok": True, "tasks": _svc().queue.list_tasks()})


@bp.get("/api/cloud/sync/<task_id>")
def api_cloud_sync_task(task_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    task = _svc().queue.get(task_id)
    if not task:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True, "task": task.to_dict()})


@bp.post("/api/cloud/sync/<task_id>/cancel")
def api_cloud_sync_cancel(task_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    ok = _svc().queue.cancel(task_id)
    return jsonify({"ok": ok})


@bp.post("/api/cloud/settings")
def api_cloud_settings():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    saved = _svc().update_settings(data)
    return jsonify({"ok": True, "settings": saved})


@bp.post("/api/cloud/post-dub")
def api_cloud_post_dub():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or data.get("output_file") or "").strip()
    if not filename:
        return jsonify({"ok": False, "error": "filename required"}), 400
    result = _svc().post_dub_action(
        filename,
        str(data.get("action") or "keep_local"),
        provider_id=data.get("provider_id"),
        title=str(data.get("title") or ""),
        subtitle_file=(data.get("subtitle_file") or "").strip() or None,
    )
    return jsonify(result)


@bp.post("/api/cloud/backup/run")
def api_cloud_backup_run():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    _svc().enqueue_backup_all()
    return jsonify({"ok": True})


@bp.post("/api/cloud/cache/apply")
def api_cloud_cache_apply():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    files = data.get("synced_files") or []
    return jsonify({"ok": True, **_svc().apply_cache_policy(synced_files=files)})


@bp.post("/api/cloud/remote/jobs")
def api_cloud_remote_jobs():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or "").strip()
    if not kind:
        return jsonify({"ok": False, "error": "kind required"}), 400
    try:
        job = _svc().submit_remote_job(
            kind,
            target=str(data.get("target") or "local"),
            project_id=str(data.get("project_id") or ""),
            provider_id=str(data.get("provider_id") or "tubedub_cloud"),
            payload=data.get("payload") or {},
        )
    except NotImplementedError as e:
        return jsonify({"ok": False, "error": str(e)}), 501
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "job": job})


@bp.get("/api/cloud/remote/jobs")
def api_cloud_remote_jobs_list():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    return jsonify({"ok": True, "jobs": _svc().remote_jobs.list_jobs()})


@bp.post("/api/cloud/projects/<project_id>/move")
def api_cloud_project_move(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    dest = (data.get("dest_provider_id") or "").strip()
    if not dest:
        return jsonify({"ok": False, "error": "dest_provider_id required"}), 400
    project = _svc().manager.move_between_providers(project_id, dest)
    return jsonify({"ok": True, "project": project.to_dict()})
