"""TubeDub AI Module API — user-facing «AI-модуль», no technical jargon."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger("tubedub.api.ai_manager")

bp = Blueprint("ai_manager_api", __name__)
APP_DIR = Path(__file__).resolve().parents[1]


@bp.get("/api/ai-module/status")
def api_ai_status():
    from engines.ai_manager import user_status

    return jsonify({"ok": True, **user_status(APP_DIR)})


@bp.get("/api/ai-module/prompt-needed")
def api_ai_prompt_needed():
    from engines.ai_manager import prompt_needed

    quality = request.args.get("quality") == "maximum"
    needed = prompt_needed(APP_DIR, quality_maximum=quality)
    from engines.ai_manager.config import ESTIMATED_DOWNLOAD_GB

    return jsonify(
        {
            "ok": True,
            "needed": needed,
            "estimated_download_gb": ESTIMATED_DOWNLOAD_GB,
            "message": (
                "Для максимального качества дубляжа требуется установить AI-модуль."
                if needed
                else None
            ),
        }
    )


@bp.post("/api/ai-module/defer")
def api_ai_defer():
    from engines.ai_manager import defer_install

    defer_install(APP_DIR)
    return jsonify({"ok": True, "deferred": True})


@bp.post("/api/ai-module/install")
def api_ai_install():
    from engines.ai_manager import start_install

    data = request.get_json(silent=True) or {}
    model = str(data.get("model") or "").strip() or None
    force = bool(data.get("reinstall"))
    kwargs = {"force_reinstall": force}
    if model:
        kwargs["model"] = model
    result = start_install(APP_DIR, **kwargs)
    if not result.get("ok"):
        return jsonify(result), 409
    return jsonify(result)


@bp.get("/api/ai-module/install/progress")
def api_ai_install_progress():
    from engines.ai_manager import user_status

    return jsonify({"ok": True, **user_status(APP_DIR)})


@bp.post("/api/ai-module/update-model")
def api_ai_update_model():
    from engines.ai_manager import start_install
    from engines.ai_manager.config import DEFAULT_MODEL

    data = request.get_json(silent=True) or {}
    # «Обновить модель» upgrades to the current recommended (fast, CPU-friendly)
    # model by default; an explicit model may still be requested.
    model = str(data.get("model") or "").strip() or DEFAULT_MODEL
    return jsonify(start_install(APP_DIR, model=model, force_reinstall=False))


@bp.delete("/api/ai-module/uninstall")
def api_ai_uninstall():
    from engines.ai_manager import uninstall

    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"ok": False, "error": "confirmation_required"}), 400
    return jsonify(uninstall(APP_DIR))


@bp.post("/api/ai-module/clear-cache")
def api_ai_clear_cache():
    from engines.ai_manager import clear_model_cache

    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"ok": False, "error": "confirmation_required"}), 400
    return jsonify(clear_model_cache(APP_DIR))


@bp.get("/api/ai-module/models")
def api_ai_models():
    from engines.ai_manager import user_status

    st = user_status(APP_DIR)
    return jsonify(
        {
            "ok": True,
            "selected_provider": st.get("selected_provider"),
            "model": st.get("model"),
            "providers": st.get("providers") or [],
            "available_models": st.get("available_models") or [],
        }
    )


@bp.post("/api/ai-module/select-model")
def api_ai_select_model():
    from engines.ai_manager import select_model

    data = request.get_json(silent=True) or {}
    provider = str(data.get("provider") or "").strip().lower()
    if not provider:
        return jsonify({"ok": False, "error": "provider_required"}), 400
    result = select_model(APP_DIR, provider)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@bp.post("/api/ai-module/select-quality")
def api_ai_select_quality():
    from engines.ai_manager import select_quality_mode

    data = request.get_json(silent=True) or {}
    mode = str(data.get("quality_mode") or data.get("mode") or "").strip().lower()
    if not mode:
        return jsonify({"ok": False, "error": "quality_mode_required"}), 400
    return jsonify(select_quality_mode(APP_DIR, mode))


@bp.post("/api/ai-module/download-deepseek")
def api_ai_download_deepseek():
    from engines.ai_manager import download_provider_model

    result = download_provider_model(APP_DIR, provider_id="deepseek")
    if not result.get("ok"):
        return jsonify(result), 409
    return jsonify(result)
