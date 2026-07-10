"""AI Sources & AI Settings API (Production Ready TZ §§4,19)."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).resolve().parents[1]
bp = Blueprint("ai_sources_api", __name__)


@bp.get("/api/ai/sources/status")
def api_sources_status():
    try:
        from core.ai_router import get_ai_router

        return jsonify({"ok": True, **get_ai_router(app_dir=str(APP_DIR)).status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/ai/sources")
def api_sources_get():
    try:
        from core.ai_sources import get_ai_sources

        return jsonify({"ok": True, "sources": get_ai_sources(APP_DIR).get().to_dict()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/ai/sources")
def api_sources_set():
    """Select Local / My API / TubeDub Cloud / Future. Default Local stays free."""
    body = request.get_json(silent=True) or {}
    try:
        from core.ai_router import get_ai_router
        from core.ai_sources import get_ai_sources

        store = get_ai_sources(APP_DIR)
        store.update(**body)
        store.apply_to_env()
        decision = get_ai_router(app_dir=str(APP_DIR)).apply_route()
        return jsonify(
            {
                "ok": True,
                "sources": store.get().to_dict(),
                "active_route": decision.to_dict(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/ai/sources/first-run")
def api_sources_first_run():
    """First-run choice: continue without local / use API / download / point to folder.

    Never downloads unless action == download_local and user confirms.
    """
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "continue_without").strip().lower()
    try:
        from core.ai_sources import AISourceMode, get_ai_sources

        store = get_ai_sources(APP_DIR)
        updates: dict = {"first_run_prompt_done": True}
        if action in ("continue_without", "mt_only"):
            updates["source_mode"] = AISourceMode.LOCAL.value
            updates["allow_mt_only"] = True
        elif action in ("use_api", "my_api"):
            updates["source_mode"] = AISourceMode.USER_API.value
            if body.get("user_api"):
                updates["user_api"] = body["user_api"]
        elif action in ("point_existing", "existing_model"):
            updates["source_mode"] = AISourceMode.LOCAL.value
            local = dict(body.get("local") or {})
            if body.get("models_dir"):
                local["models_dir"] = body["models_dir"]
            if body.get("model"):
                local["model"] = body["model"]
            if body.get("base_url"):
                local["base_url"] = body["base_url"]
            updates["local"] = local
        elif action in ("download_local", "install_ollama"):
            # Explicit opt-in only — still do not auto-pull weights here.
            updates["source_mode"] = AISourceMode.LOCAL.value
            store.update(**updates)
            store.apply_to_env()
            started = False
            err = ""
            if body.get("confirm_install"):
                try:
                    from engines.ai_manager.manager import start_install

                    start_install(APP_DIR)
                    started = True
                except Exception as exc:  # noqa: BLE001
                    err = str(exc)
            return jsonify(
                {
                    "ok": True,
                    "action": action,
                    "install_started": started,
                    "error": err,
                    "sources": store.get().to_dict(),
                    "note": "Models are never auto-downloaded without confirmation.",
                }
            )
        else:
            return jsonify({"ok": False, "error": f"unknown action: {action}"}), 400

        store.update(**updates)
        store.apply_to_env()
        return jsonify({"ok": True, "action": action, "sources": store.get().to_dict()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/ai/settings")
def api_ai_settings_get():
    try:
        from core.ai_router import get_ai_router, list_supported_providers
        from core.ai_sources import get_ai_sources

        cfg = get_ai_sources(APP_DIR).get()
        return jsonify(
            {
                "ok": True,
                "settings": {
                    "source_mode": cfg.source_mode,
                    "quality_mode": cfg.quality_mode,
                    "provider": cfg.user_api.provider
                    if cfg.source_mode == "user_api"
                    else cfg.local.provider,
                    "model": cfg.user_api.model
                    if cfg.source_mode == "user_api"
                    else cfg.local.model,
                    "base_url": cfg.user_api.base_url
                    if cfg.source_mode == "user_api"
                    else cfg.local.base_url,
                    "api_key_set": bool(cfg.user_api.api_key),
                    "temperature": cfg.user_api.temperature,
                    "max_tokens": cfg.user_api.max_tokens,
                    "context_tokens": cfg.user_api.context_tokens,
                    "streaming": cfg.user_api.streaming,
                    "reasoning": cfg.user_api.reasoning,
                    "models_dir": cfg.local.models_dir,
                },
                "providers": list_supported_providers(),
                "recommended": get_ai_router(app_dir=str(APP_DIR)).recommend_model(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/ai/settings")
def api_ai_settings_set():
    body = request.get_json(silent=True) or {}
    try:
        from core.ai_router import get_ai_router
        from core.ai_sources import get_ai_sources

        store = get_ai_sources(APP_DIR)
        updates: dict = {}
        if body.get("source_mode"):
            updates["source_mode"] = body["source_mode"]
        if body.get("quality_mode"):
            updates["quality_mode"] = body["quality_mode"]
        mode = str(body.get("source_mode") or store.get().source_mode)
        if mode == "user_api":
            ua = {}
            for k in (
                "provider",
                "api_key",
                "base_url",
                "model",
                "temperature",
                "max_tokens",
                "context_tokens",
                "streaming",
                "reasoning",
            ):
                if k in body:
                    ua[k] = body[k]
            if ua:
                updates["user_api"] = ua
        else:
            local = {}
            for k in ("provider", "model", "base_url", "models_dir"):
                if k in body:
                    local[k] = body[k]
            if local:
                updates["local"] = local
        store.update(**updates)
        store.apply_to_env()
        decision = get_ai_router(app_dir=str(APP_DIR)).apply_route()
        return jsonify(
            {
                "ok": True,
                "settings": store.get().to_dict(),
                "active_route": decision.to_dict(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/ai/settings/test")
def api_ai_settings_test():
    """Connection check — no downloads, no paywall."""
    try:
        from core.ai_router import get_ai_router
        from engines.llm_providers.transport import chat_completion

        decision = get_ai_router(app_dir=str(APP_DIR)).apply_route()
        if not decision.available:
            return jsonify(
                {
                    "ok": True,
                    "connected": False,
                    "route": decision.to_dict(),
                    "message": "LLM unavailable — local Marian MT still works free.",
                }
            )
        text = chat_completion(
            "Reply with OK",
            system="Reply with exactly: OK",
            model=decision.model,
            max_tokens=8,
            temperature=0.0,
            timeout=30.0,
            transport={
                "kind": decision.kind,
                "base_url": decision.base_url,
                "provider": decision.provider,
                "model": decision.model,
                "api_key": decision.api_key,
            },
        )
        return jsonify(
            {
                "ok": True,
                "connected": bool(text),
                "reply": text,
                "route": decision.to_dict(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/ai/recommend")
def api_ai_recommend():
    try:
        from core.ai_router import get_ai_router

        return jsonify(
            {"ok": True, "recommendation": get_ai_router(app_dir=str(APP_DIR)).recommend_model()}
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/ai/benchmark")
def api_ai_benchmark():
    body = request.get_json(silent=True) or {}
    try:
        from core.ai_benchmark import DEFAULT_SAMPLE, run_ai_benchmark

        sample = str(body.get("sample") or "").strip() or DEFAULT_SAMPLE
        result = run_ai_benchmark(
            sample=sample,
            src_lang=str(body.get("src_lang") or "en"),
            tgt_lang=str(body.get("tgt_lang") or "uk"),
            models=body.get("models"),
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500
