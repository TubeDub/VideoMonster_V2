"""TubeDub AI Manager — orchestration (v1.0).

User-facing concept: «AI-модуль TubeDub». Internal backends (Ollama, LM Studio,
OpenAI-compatible) are never shown in the product UI.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.ai_manager.config import (
    BACKEND_LABEL,
    DEFAULT_MODEL,
    ESTIMATED_DOWNLOAD_GB,
    STATUS_ERROR,
    STATUS_INSTALLING,
    STATUS_NOT_INSTALLED,
    STATUS_READY,
    append_log,
    load_config,
    save_config,
    set_progress,
)
from engines.ai_manager import installer

logger = logging.getLogger("tubedub.ai_manager")

_install_lock = threading.Lock()
_install_running = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_ai_ready(app_dir: Path) -> bool:
    cfg = load_config(app_dir)
    if cfg.get("status") == STATUS_READY:
        return True
    # While an install is genuinely in progress the model may answer /v1/models
    # but still fail real generation — do NOT report ready, or the dub will run
    # degraded against a half-installed model.
    if cfg.get("status") == STATUS_INSTALLING and _install_running:
        return False
    try:
        from engines.translation_adapt import llm_rephrase_available

        if llm_rephrase_available():
            return True
    except Exception:
        pass
    return False


def reconcile_install_state(app_dir: Path) -> dict[str, Any]:
    """Repair a stuck «installing» status left by an interrupted install.

    If the app was restarted (or crashed) mid-install, the daemon install
    thread dies but ``ai_module.json`` keeps ``status=installing`` forever. On
    startup we reconcile against reality: if the backend now answers a real
    generation request → mark READY; otherwise → ERROR so the user can retry.
    Safe no-op when an install is actually running in this process.
    """
    cfg = load_config(app_dir)
    if cfg.get("status") != STATUS_INSTALLING or _install_running:
        return {"changed": False, "status": cfg.get("status")}

    model = cfg.get("model") or DEFAULT_MODEL
    try:
        installer.verify_ai_module(app_dir, model)
        verified = True
    except Exception as exc:
        verified = False
        verify_err = str(exc)[:200]

    if verified:
        cfg = load_config(app_dir)
        cfg["status"] = STATUS_READY
        cfg["installed_at"] = cfg.get("installed_at") or _utc_now()
        cfg["last_verification"] = {"ok": True, "at": _utc_now(), "recovered": True}
        cfg["size_bytes"] = installer.ai_module_disk_bytes(app_dir)
        save_config(app_dir, cfg)
        append_log(app_dir, "Install state recovered → ready (post-restart)")
        return {"changed": True, "status": STATUS_READY}

    cfg = load_config(app_dir)
    cfg["status"] = STATUS_ERROR
    cfg["last_error"] = (
        "Установка была прервана (перезапуск программы). "
        "Нажмите «Переустановить» в настройках AI Module."
    )
    save_config(app_dir, cfg)
    append_log(app_dir, f"Install state recovered → error: {verify_err}", level="warn")
    return {"changed": True, "status": STATUS_ERROR}


def _detect_existing_backend() -> dict[str, Any] | None:
    try:
        from engines.llm_adaptation_mode import discover_local_llm

        return discover_local_llm(force=True)
    except Exception:
        return None


def prompt_needed(app_dir: Path, *, quality_maximum: bool = False) -> bool:
    """True when first-run (or re-prompt) install dialog should appear."""
    if is_ai_ready(app_dir):
        return False
    cfg = load_config(app_dir)
    if cfg.get("deferred") and not quality_maximum:
        return False
    if cfg.get("status") == STATUS_INSTALLING:
        return False
    return True


def _available_models() -> list[str]:
    disc = _detect_existing_backend()
    if disc:
        return list(disc.get("models") or [])
    return []


def _reset_llm_runtime_caches() -> None:
    try:
        from engines.translation_adapt import reset_endpoint_cache

        reset_endpoint_cache()
    except Exception:
        pass
    try:
        import engines.llm_adaptation_mode as lam

        lam._discovery_cache["ts"] = 0.0
        lam._discovery_cache["result"] = None
    except Exception:
        pass


def select_quality_mode(app_dir: Path, mode: str) -> dict[str, Any]:
    """Set dub adaptation quality (fast / balanced / max_quality)."""
    from engines.llm_providers.registry import save_quality_mode
    from engines.translation_adapt import normalize_speed_mode, reset_endpoint_cache

    normalized = normalize_speed_mode(mode)
    save_quality_mode(normalized, app_dir=app_dir)
    _reset_llm_runtime_caches()
    try:
        from engines.translation_adapt import configure_adaptation_budget

        configure_adaptation_budget(mode=normalized)
    except Exception:
        pass
    append_log(app_dir, f"AI quality mode: {normalized}")
    return {"ok": True, "quality_mode": normalized}


def select_model(app_dir: Path, provider_id: str) -> dict[str, Any]:
    """Switch the active model family without restarting the app."""
    from engines.llm_providers.registry import get_provider, save_persisted_selection

    prov = get_provider(provider_id)
    if prov is None:
        return {"ok": False, "error": "unknown_provider"}

    available = _available_models()
    installed = prov.resolve_installed_model(available)
    model = installed or prov.default_model

    save_persisted_selection(provider_id=prov.family_id, model=model, app_dir=app_dir)
    cfg = load_config(app_dir)
    cfg["selected_provider"] = prov.family_id
    cfg["model"] = model
    save_config(app_dir, cfg)
    _reset_llm_runtime_caches()
    append_log(app_dir, f"Selected AI model: {prov.display_name} ({model})")

    return {
        "ok": True,
        "provider": prov.family_id,
        "model": model,
        "installed": bool(installed),
        "needs_download": not bool(installed),
    }


def download_provider_model(app_dir: Path, provider_id: str = "deepseek") -> dict[str, Any]:
    """Pull the default model for a provider family (e.g. DeepSeek on first run)."""
    from engines.llm_providers.registry import DEFAULT_FAMILY_ID, get_provider

    prov = get_provider(provider_id or DEFAULT_FAMILY_ID)
    if prov is None:
        return {"ok": False, "error": "unknown_provider"}
    return start_install(app_dir, model=prov.default_model, force_reinstall=False)


def user_status(app_dir: Path) -> dict[str, Any]:
    """Status payload for Settings «AI Module» — no technical terms."""
    # Heal a stuck «installing» status (e.g. after an app restart mid-install).
    reconcile_install_state(app_dir)
    cfg = load_config(app_dir)
    ready = is_ai_ready(app_dir)
    status = cfg.get("status") or STATUS_NOT_INSTALLED
    if ready and status != STATUS_INSTALLING:
        status = STATUS_READY
        # Avoid a contradictory card: once the module is ready, a stale
        # «не отвечает» error or a frozen «Проверка… 92%» progress must not
        # linger. Heal the persisted config so the UI is self-consistent.
        if cfg.get("last_error") or cfg.get("install_progress") or cfg.get("status") != STATUS_READY:
            cfg["status"] = STATUS_READY
            cfg["last_error"] = None
            cfg["install_progress"] = {}
            cfg["installed_at"] = cfg.get("installed_at") or _utc_now()
            save_config(app_dir, cfg)

    labels = {
        STATUS_NOT_INSTALLED: "Не установлен",
        STATUS_INSTALLING: "Устанавливается",
        STATUS_READY: "Готов к работе",
        STATUS_ERROR: "Ошибка установки",
    }

    size = installer.ai_module_disk_bytes(app_dir)
    disc = _detect_existing_backend()
    available_models = list(disc.get("models") or []) if disc else []

    try:
        from engines.llm_providers.registry import (
            DEFAULT_FAMILY_ID,
            list_providers_for_ui,
            load_persisted_selection,
        )

        selection = load_persisted_selection(app_dir)
        providers = list_providers_for_ui(available_models)
        selected_provider = selection.get("provider") or DEFAULT_FAMILY_ID
        quality_mode = selection.get("quality_mode") or "max_quality"
    except Exception:
        providers = []
        selected_provider = "deepseek"
        quality_mode = "max_quality"

    return {
        "status": status,
        "status_label": labels.get(status, status),
        "ready": ready,
        "deferred": bool(cfg.get("deferred")),
        "backend_label": BACKEND_LABEL if ready or disc else None,
        "model": cfg.get("model") or DEFAULT_MODEL,
        "selected_provider": selected_provider,
        "quality_mode": quality_mode,
        "providers": providers,
        "available_models": available_models,
        "size_bytes": size,
        "size_mb": round(size / 1024**2, 1),
        "size_gb": round(size / 1024**3, 2),
        "installed_at": cfg.get("installed_at"),
        "install_progress": cfg.get("install_progress") or {},
        "last_error": cfg.get("last_error"),
        "estimated_download_gb": ESTIMATED_DOWNLOAD_GB,
        "intelligent_adaptation_available": ready,
    }


def defer_install(app_dir: Path) -> None:
    cfg = load_config(app_dir)
    cfg["deferred"] = True
    cfg["deferred_at"] = _utc_now()
    save_config(app_dir, cfg)
    append_log(app_dir, "User chose Later")


def _install_worker(app_dir: Path, *, model: str, force_reinstall: bool) -> None:
    global _install_running
    cfg = load_config(app_dir)
    cfg["status"] = STATUS_INSTALLING
    cfg["deferred"] = False
    cfg["last_error"] = None
    cfg["model"] = model
    save_config(app_dir, cfg)
    try:
        existing = _detect_existing_backend()
        if existing and not force_reinstall:
            append_log(app_dir, "Using existing compatible local AI")
            installer.use_existing_backend(app_dir, model=model)
            cfg["installed_by_tubedub"] = False
            cfg["backend_internal"] = existing.get("provider")
        else:
            append_log(app_dir, "Starting full AI module install")
            installer.install_ai_module(app_dir, model=model)
            cfg["installed_by_tubedub"] = True
            cfg["backend_internal"] = "tubedub_embedded"
        verify = installer.verify_ai_module(app_dir, model)
        cfg = load_config(app_dir)
        cfg["status"] = STATUS_READY
        cfg["installed_at"] = _utc_now()
        cfg["last_error"] = None
        cfg["last_verification"] = {"ok": True, "at": _utc_now(), **verify}
        cfg["size_bytes"] = installer.ai_module_disk_bytes(app_dir)
        # Set progress in the same dict we persist, otherwise the later
        # save_config would clobber it back to the stale «Проверка… 92%».
        cfg["install_progress"] = {"phase": "done", "percent": 100, "message": "AI-модуль успешно установлен"}
        save_config(app_dir, cfg)
        append_log(app_dir, "Install complete")
    except Exception as exc:
        logger.exception("AI module install failed")
        cfg = load_config(app_dir)
        cfg["status"] = STATUS_ERROR
        cfg["last_error"] = str(exc)[:500]
        save_config(app_dir, cfg)
        append_log(app_dir, str(exc), level="error")
        set_progress(app_dir, "error", 0, str(exc)[:200])
    finally:
        _install_running = False


def start_install(
    app_dir: Path,
    *,
    model: str = DEFAULT_MODEL,
    force_reinstall: bool = False,
) -> dict[str, Any]:
    global _install_running
    with _install_lock:
        if _install_running:
            return {"ok": False, "error": "install_in_progress"}
        _install_running = True
    t = threading.Thread(
        target=_install_worker,
        args=(app_dir,),
        kwargs={"model": model, "force_reinstall": force_reinstall},
        daemon=True,
    )
    t.start()
    return {"ok": True, "started": True}


def uninstall(app_dir: Path) -> dict[str, Any]:
    cfg = load_config(app_dir)
    freed = installer.uninstall_ai_module(
        app_dir, installed_by_tubedub=bool(cfg.get("installed_by_tubedub"))
    )
    fresh = default_config_reset()
    save_config(app_dir, fresh)
    append_log(app_dir, f"Uninstalled, freed {freed} bytes")
    return {"ok": True, "bytes_freed": freed}


def default_config_reset() -> dict[str, Any]:
    from engines.ai_manager.config import default_config

    return default_config()


def clear_model_cache(app_dir: Path) -> dict[str, Any]:
    from engines.ai_manager.installer import _ollama_models_dir
    import shutil

    freed = 0
    md = _ollama_models_dir()
    if md.is_dir():
        freed = installer._dir_size(md)
        shutil.rmtree(md, ignore_errors=True)
    cache = Path(app_dir) / "data" / "cache" / "llm_rewrite_cache.json"
    if cache.is_file():
        try:
            freed += cache.stat().st_size
            cache.unlink()
        except OSError:
            pass
    append_log(app_dir, "Model cache cleared")
    return {"ok": True, "bytes_freed": freed}


def build_openddf_ai_installation(app_dir: Path, task_info: dict | None = None) -> dict[str, Any]:
    """OpenDDF «AI Installation» block — sanitized labels for operators."""
    cfg = load_config(app_dir)
    info = task_info or {}
    ai_evt = info.get("ai_installation") or {}
    st = user_status(app_dir)
    return {
        "backend_label": st.get("backend_label") or BACKEND_LABEL,
        "model": st.get("model"),
        "status": st.get("status"),
        "status_label": st.get("status_label"),
        "installed_at": cfg.get("installed_at"),
        "verification": cfg.get("last_verification"),
        "last_error": cfg.get("last_error"),
        "install_log": (cfg.get("install_log") or [])[-20:],
        "install_progress": cfg.get("install_progress"),
        "retries": len([e for e in (cfg.get("install_log") or []) if e.get("level") == "error"]),
        "task_event": ai_evt or None,
        "intelligent_adaptation_available": st.get("ready"),
    }
