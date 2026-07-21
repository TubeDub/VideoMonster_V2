"""Ensure a callable LLM model exists before adaptation runs.

Root cause fix for ``model_missing``: endpoint discovery used to succeed when
Ollama/LM Studio was reachable, but the resolved model tag was not installed.
This module validates the model list, remaps to an installed tag, optionally
pulls a missing model, and records run-scoped provider status for diagnostics.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.llm_callable")

_RUN_LOCK = threading.RLock()
_RUN_STATE: dict[str, Any] = {
    "callable": False,
    "llm_available": False,
    "provider": "",
    "model": "",
    "base_url": "",
    "installed_models": [],
    "remediation": "",
    "attempts": 0,
    "fatal_reason": "",
    "checked_at": 0.0,
    "health": {},
}


def get_run_state() -> dict[str, Any]:
    with _RUN_LOCK:
        return dict(_RUN_STATE)


def reset_run_state() -> None:
    with _RUN_LOCK:
        _RUN_STATE.update(
            {
                "callable": False,
                "llm_available": False,
                "provider": "",
                "model": "",
                "base_url": "",
                "installed_models": [],
                "remediation": "",
                "attempts": 0,
                "fatal_reason": "",
                "checked_at": 0.0,
                "health": {},
            }
        )


def _auto_pull_enabled() -> bool:
    raw = str(os.getenv("VM_LLM_AUTO_PULL") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def refresh_endpoint_models(*, force: bool = False) -> dict[str, Any]:
    """Re-discover endpoint and refresh installed model tags."""
    try:
        from engines.translation_adapt import reset_endpoint_cache

        reset_endpoint_cache()
    except Exception:
        pass
    try:
        from engines.llm_adaptation_mode import discover_local_llm, resolve_llm_endpoint

        discover_local_llm(force=force)
        ep = resolve_llm_endpoint()
        models = list(ep.get("models") or [])
        if not models and ep.get("available"):
            models = _fetch_models_for_endpoint(ep)
            ep["models"] = models
        return ep
    except Exception as exc:
        logger.debug("refresh_endpoint_models failed: %s", exc)
        return {"available": False, "models": [], "provider": "none", "base_url": ""}


def _fetch_models_for_endpoint(ep: dict[str, Any]) -> list[str]:
    provider = str(ep.get("provider") or "")
    base = str(ep.get("base_url") or "").rstrip("/")
    if not base:
        return []
    try:
        from engines.llm_adaptation_mode import _http_get_json, _parse_models

        if provider == "ollama":
            payload = _http_get_json(base.replace("/v1", "") + "/api/tags", timeout=1.5)
            return _parse_models(payload, "ollama")
        payload = _http_get_json(base + "/models", timeout=1.5)
        return _parse_models(payload, "openai")
    except Exception:
        return []


def _model_listed(model: str, available: list[str]) -> bool:
    if not model:
        return False
    if not available:
        return False
    low = {m.lower(): m for m in available}
    m = str(model).strip()
    if m.lower() in low:
        return True
    base = m.split(":", 1)[0].lower()
    return any(tag.lower().startswith(base + ":") or tag.lower() == base for tag in available)


def _pin_model(model: str) -> None:
    if model:
        os.environ["VM_TRANSLATE_MODEL"] = model
    try:
        from engines.translation_adapt import reset_endpoint_cache

        reset_endpoint_cache()
    except Exception:
        pass


def _try_pull_model(model: str, app_dir: Path | None) -> bool:
    if not model or not _auto_pull_enabled():
        return False
    root = app_dir or Path(__file__).resolve().parents[1]
    try:
        from engines.ai_manager.installer import _pull_model

        logger.warning("[LLM Callable] Pulling missing model %s", model)
        _pull_model(model, root)
        return True
    except Exception as exc:
        logger.warning("[LLM Callable] Model pull failed for %s: %s", model, exc)
        return False


def remediate_missing_model(
    preferred: str,
    *,
    app_dir: Path | None = None,
    available: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    """Return (resolved_model, remediation, installed_models).

    remediation: installed | remapped | pulled | none | fatal
    """
    ep = refresh_endpoint_models(force=True)
    tags = list(available if available is not None else ep.get("models") or [])
    if not tags:
        tags = _fetch_models_for_endpoint(ep)

    pref = str(preferred or "").strip()
    if pref and _model_listed(pref, tags):
        _pin_model(pref)
        return pref, "installed", tags

    try:
        from engines.llm_providers.registry import resolve_model

        resolved = resolve_model(
            tags,
            provider=str(ep.get("provider") or ""),
            app_dir=app_dir,
        )
    except Exception:
        resolved = ""

    if resolved and _model_listed(resolved, tags):
        _pin_model(resolved)
        if pref and resolved != pref:
            logger.warning("[LLM Callable] Remapped model %s → %s", pref, resolved)
            return resolved, "remapped", tags
        return resolved, "installed", tags

    if tags:
        pick = tags[0]
        _pin_model(pick)
        logger.warning("[LLM Callable] Fallback to first installed model: %s", pick)
        return pick, "remapped", tags

    if pref and str(ep.get("provider") or "") == "ollama" and _try_pull_model(pref, app_dir):
        ep2 = refresh_endpoint_models(force=True)
        tags2 = list(ep2.get("models") or []) or _fetch_models_for_endpoint(ep2)
        if _model_listed(pref, tags2):
            _pin_model(pref)
            return pref, "pulled", tags2

    cloud_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("VM_LLM_API_KEY")
        or os.getenv("VM_OPENAI_API_KEY")
    )
    if cloud_key:
        cloud_model = os.getenv("VM_LLM_CLOUD_MODEL") or os.getenv("VM_TRANSLATE_MODEL") or "gpt-4o-mini"
        _pin_model(cloud_model)
        return cloud_model, "cloud_fallback", []

    return "", "fatal", tags


def probe_model_callable(model: str, *, provider: str = "") -> dict[str, Any]:
    if not model:
        return {"callable": False, "failure_phase": "model_missing"}
    prov = str(provider or "").lower()
    if prov == "ollama" or "11434" in str(os.getenv("VM_LLM_BASE_URL") or ""):
        try:
            from engines.llm_retry_manager import probe_ollama_detailed

            health = probe_ollama_detailed(model)
            health["callable"] = bool(health.get("model_listed"))
            return health
        except Exception as exc:
            return {"callable": False, "failure_phase": "unknown", "error": str(exc)}
    return {"callable": True, "failure_phase": "responding"}


def ensure_llm_callable(
    *,
    app_dir: Path | None = None,
    task_id: str = "",
    max_attempts: int = 3,
    allow_pull: bool | None = None,
) -> dict[str, Any]:
    """Validate provider + model with retries before adaptation.

    Sets run-scoped state consumed by ``llm_rephrase_available()`` and diagnostics.
    """
    if allow_pull is None:
        allow_pull = _auto_pull_enabled()

    attempts = max(1, int(max_attempts or 3))
    last_reason = ""
    remediation = ""
    resolved = ""
    preferred = ""
    ep: dict[str, Any] = {}
    health: dict[str, Any] = {}

    for attempt in range(1, attempts + 1):
        ep = refresh_endpoint_models(force=attempt > 1)
        if not ep.get("available"):
            last_reason = "no_endpoint"
            time.sleep(0.5 * attempt)
            continue

        try:
            from engines.llm_adaptation_mode import resolve_llm_model

            preferred = resolve_llm_model(ep.get("models"), provider=ep.get("provider", ""))
        except Exception:
            preferred = os.getenv("VM_TRANSLATE_MODEL", "")

        tags = list(ep.get("models") or []) or _fetch_models_for_endpoint(ep)
        if preferred and _model_listed(preferred, tags):
            resolved, remediation, tags = preferred, "installed", tags
        else:
            resolved, remediation, tags = remediate_missing_model(
                preferred,
                app_dir=app_dir,
                available=tags,
            )
            if remediation == "fatal" and allow_pull and preferred:
                if _try_pull_model(preferred, app_dir):
                    ep = refresh_endpoint_models(force=True)
                    tags = list(ep.get("models") or []) or _fetch_models_for_endpoint(ep)
                    if _model_listed(preferred, tags):
                        resolved, remediation = preferred, "pulled"

        if not resolved:
            last_reason = "model_missing"
            time.sleep(0.75 * attempt)
            continue

        health = probe_model_callable(resolved, provider=str(ep.get("provider") or ""))
        if health.get("callable"):
            status = {
                "callable": True,
                "llm_available": True,
                "provider": str(ep.get("provider") or ""),
                "model": resolved,
                "base_url": str(ep.get("base_url") or ""),
                "installed_models": tags,
                "remediation": remediation or "installed",
                "attempts": attempt,
                "fatal_reason": "",
                "checked_at": time.time(),
                "health": health,
                "task_id": task_id,
            }
            with _RUN_LOCK:
                _RUN_STATE.update(status)
            logger.info(
                "[LLM Callable] task=%s provider=%s model=%s remediation=%s attempt=%d",
                task_id or "-",
                status["provider"],
                status["model"],
                status["remediation"],
                attempt,
            )
            return status

        last_reason = str(health.get("failure_phase") or "model_missing")
        time.sleep(0.75 * attempt)

    status = {
        "callable": False,
        "llm_available": bool(ep.get("available")),
        "provider": str(ep.get("provider") or ""),
        "model": resolved or preferred,
        "base_url": str(ep.get("base_url") or ""),
        "installed_models": list(ep.get("models") or []),
        "remediation": remediation or "none",
        "attempts": attempts,
        "fatal_reason": last_reason or "provider_fatal",
        "checked_at": time.time(),
        "health": health,
        "task_id": task_id,
    }
    with _RUN_LOCK:
        _RUN_STATE.update(status)
    logger.error(
        "[LLM Callable] FAILED task=%s reason=%s provider=%s model=%s",
        task_id or "-",
        status["fatal_reason"],
        status["provider"],
        status["model"],
    )
    return status


def is_llm_callable(*, quick: bool = False) -> bool:
    """Whether adaptation may invoke the LLM right now."""
    state = get_run_state()
    if state.get("checked_at"):
        return bool(state.get("callable"))
    if quick:
        try:
            from engines.llm_adaptation_mode import resolve_llm_endpoint, resolve_llm_model

            ep = resolve_llm_endpoint()
            if not ep.get("available"):
                return False
            model = resolve_llm_model(ep.get("models"), provider=ep.get("provider", ""))
            tags = list(ep.get("models") or [])
            if tags:
                return _model_listed(model, tags)
            return bool(model)
        except Exception:
            return False
    status = ensure_llm_callable(max_attempts=1)
    return bool(status.get("callable"))


def apply_to_task_info(info: dict[str, Any], status: dict[str, Any]) -> None:
    info["llm_callable"] = bool(status.get("callable"))
    info["llm_provider_status"] = {
        "provider": status.get("provider"),
        "model": status.get("model"),
        "base_url": status.get("base_url"),
        "installed_models": status.get("installed_models") or [],
        "remediation": status.get("remediation"),
        "attempts": status.get("attempts"),
        "fatal_reason": status.get("fatal_reason"),
        "health": status.get("health") or {},
    }
