"""Model manager UI API — Settings → Модели."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("models_api", __name__)


def _dev_extra(comp: dict) -> dict:
    import os

    if os.getenv("VM_DEV_MODE", "").strip().lower() not in ("1", "true", "yes", "on") and (
        os.getenv("VM_ARCHITECT_MODE", "").strip().lower() not in ("1", "true", "yes", "on")
    ):
        return {}
    return {
        "path": comp.get("path", ""),
        "artifact_id": comp.get("artifact_id", ""),
        "engine_hint": comp.get("engine_hint", ""),
    }


@bp.get("/api/models/components")
def api_models_list():
    from engines.model_manager import get_storage_status, list_components

    comps = list_components(APP_DIR)
    storage = get_storage_status(APP_DIR)
    out = []
    for c in comps:
        row = {
            "id": c.get("id"),
            "variant": c.get("variant"),
            "label": c.get("label"),
            "size_mb": c.get("size_mb", 0),
            "last_used": c.get("last_used", ""),
            "status": c.get("status", "ready"),
            "languages": c.get("variant", ""),
        }
        row.update(_dev_extra(c))
        out.append(row)
    return jsonify(
        {
            "components": out,
            "storage": {
                "root": storage.get("storage_root"),
                "total_gb": storage.get("total_gb"),
                "max_gb": storage.get("max_storage_gb"),
                "disk_free_gb": storage.get("disk_free_gb"),
            },
        }
    )


@bp.post("/api/models/delete")
def api_models_delete():
    from engines.model_manager import delete_component

    data = request.get_json(silent=True) or {}
    cid = str(data.get("id") or "").strip()
    variant = str(data.get("variant") or "").strip()
    if not cid or not variant:
        return jsonify({"error": "id and variant required"}), 400
    if not data.get("confirmed"):
        return jsonify({"error": "confirmation_required", "needs_confirm": True}), 400
    result = delete_component(APP_DIR, cid, variant, force=True)
    status = 200 if result.get("ok") else 404
    return jsonify(result), status


@bp.post("/api/models/update")
def api_models_update():
    from engines.model_manager import update_component
    from engines.model_manager.runtime import prepare_download_session

    data = request.get_json(silent=True) or {}
    cid = str(data.get("id") or "").strip()
    variant = str(data.get("variant") or "").strip()
    if not cid or not variant:
        return jsonify({"error": "id and variant required"}), 400
    with prepare_download_session():
        result = update_component(APP_DIR, cid, variant)
    return jsonify(result)


@bp.post("/api/models/cleanup-unused")
def api_models_cleanup_unused():
    from engines.model_manager import apply_lru_if_allowed

    data = request.get_json(silent=True) or {}
    confirmed = bool(data.get("confirmed", True))
    result = apply_lru_if_allowed(APP_DIR, confirmed=confirmed)
    status = 200 if result.get("ok") or result.get("needs_confirm") else 400
    return jsonify(result), status


@bp.post("/api/models/cleanup-all")
def api_models_cleanup_all():
    from engines.model_manager import apply_cleanup, list_components

    data = request.get_json(silent=True) or {}
    if not data.get("confirmed"):
        return jsonify({"error": "confirmation_required"}), 400
    keys = [f"{c['id']}:{c['variant']}" for c in list_components(APP_DIR) if c.get("id")]
    result = apply_cleanup(APP_DIR, keys, confirmed=True)
    return jsonify(result)


@bp.post("/api/models/download")
def api_models_download():
    """On-demand download for a language pair (Settings or first-use)."""
    from engines.model_manager import ensure_profile
    from engines.model_manager.estimate import estimate_profile_download_mb
    from engines.model_manager.runtime import prepare_download_session

    data = request.get_json(silent=True) or {}
    src = data.get("source_lang") or "en"
    tgt = data.get("target_lang") or "ru"
    feature = str(data.get("feature") or "translate").strip().lower()
    whisper = data.get("whisper_size") or "tiny"

    est = estimate_profile_download_mb(
        APP_DIR, src, tgt, whisper_size=whisper, feature=feature
    )
    with prepare_download_session():
        result = ensure_profile(
            APP_DIR,
            src,
            tgt,
            whisper_size=whisper,
            feature=feature,
            job_id="manual",
        )
    if not result.ready:
        return jsonify({"ok": False, "error": result.error, "estimated_mb": est}), 500
    return jsonify({"ok": True, "estimated_mb": est, "components": result.components})
