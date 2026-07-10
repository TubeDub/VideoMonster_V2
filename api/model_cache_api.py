"""Model cache management API."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("model_cache_api", __name__)


@bp.get("/api/models/cache/status")
def api_cache_status():
    from engines.model_cache import cache_status

    return jsonify(cache_status(APP_DIR))


@bp.get("/api/models/cache/list")
def api_cache_list():
    from engines.model_cache import list_models

    return jsonify({"models": list_models(APP_DIR)})


@bp.post("/api/models/cache/cleanup")
def api_cache_cleanup():
    from engines.model_cache import cleanup_temp_and_corrupted, enforce_size_limit

    tmp = cleanup_temp_and_corrupted(APP_DIR)
    lru = enforce_size_limit(APP_DIR)
    return jsonify({"ok": True, "temp": tmp, "lru": lru})


@bp.post("/api/models/cache/limit")
def api_cache_set_limit():
    from engines.model_cache import set_max_cache_gb

    data = request.get_json(silent=True) or {}
    gb = float(data.get("max_cache_gb", 10))
    set_max_cache_gb(APP_DIR, gb)
    return jsonify({"ok": True, "max_cache_gb": gb})


@bp.delete("/api/models/cache/<path:model_id>")
def api_cache_delete(model_id: str):
    from engines.model_cache import delete_model

    result = delete_model(APP_DIR, model_id.replace("|", "/"))
    status = 200 if result.get("ok") else 404
    return jsonify(result), status


@bp.post("/api/models/cache/redownload")
def api_cache_redownload():
    from engines.model_cache import redownload_model
    from engines.model_manager.runtime import prepare_download_session

    data = request.get_json(silent=True) or {}
    model_id = str(data.get("model_id", "")).strip()
    engine = str(data.get("engine", "marian")).strip()
    if not model_id:
        return jsonify({"ok": False, "error": "model_id required"}), 400
    with prepare_download_session():
        result = redownload_model(APP_DIR, model_id, engine=engine)
    status = 200 if result.get("ok") else 500
    return jsonify(result), status
