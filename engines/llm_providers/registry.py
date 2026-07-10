"""LLM provider registry — single entry point for model family selection."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from engines.llm_providers.base import LLMProvider
from engines.llm_providers.deepseek import PROVIDER as DEEPSEEK
from engines.llm_providers.llama import PROVIDER as LLAMA
from engines.llm_providers.qwen import PROVIDER as QWEN

logger = logging.getLogger("tubedub.llm_providers")

DEFAULT_FAMILY_ID = "deepseek"
FALLBACK_FAMILY_ORDER: tuple[str, ...] = ("deepseek", "qwen", "llama")

_PROVIDERS: dict[str, LLMProvider] = {
    DEEPSEEK.family_id: DEEPSEEK,
    QWEN.family_id: QWEN,
    LLAMA.family_id: LLAMA,
}


def get_provider(family_id: str | None) -> LLMProvider | None:
    fid = str(family_id or "").strip().lower()
    return _PROVIDERS.get(fid)


def list_providers() -> list[LLMProvider]:
    return [_PROVIDERS[fid] for fid in FALLBACK_FAMILY_ORDER if fid in _PROVIDERS]


def list_providers_for_ui(available_models: list[str] | None = None) -> list[dict[str, Any]]:
    """UI payload: provider labels + whether an installed tag exists."""
    available = list(available_models or [])
    out: list[dict[str, Any]] = []
    for prov in list_providers():
        row = prov.to_dict()
        installed = prov.resolve_installed_model(available)
        row["installed"] = bool(installed)
        row["installed_model"] = installed or ""
        row["is_default"] = prov.family_id == DEFAULT_FAMILY_ID
        out.append(row)
    return out


def _app_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def load_persisted_selection(app_dir: Path | None = None) -> dict[str, str]:
    """Read selected provider/model from ai_module.json."""
    root = app_dir or _app_dir()
    try:
        from engines.ai_manager.config import DEFAULT_QUALITY_MODE, load_config

        cfg = load_config(root)
        provider = str(cfg.get("selected_provider") or DEFAULT_FAMILY_ID).lower()
        model = str(cfg.get("model") or "").strip()
        quality_mode = str(cfg.get("quality_mode") or DEFAULT_QUALITY_MODE).lower()
        return {"provider": provider, "model": model, "quality_mode": quality_mode}
    except Exception:
        return {"provider": DEFAULT_FAMILY_ID, "model": "", "quality_mode": "max_quality"}


def load_quality_mode(app_dir: Path | None = None) -> str:
    return load_persisted_selection(app_dir).get("quality_mode") or "max_quality"


def save_persisted_selection(
    *,
    provider_id: str,
    model: str,
    app_dir: Path | None = None,
) -> None:
    root = app_dir or _app_dir()
    from engines.ai_manager.config import load_config, save_config

    cfg = load_config(root)
    cfg["selected_provider"] = str(provider_id or DEFAULT_FAMILY_ID).lower()
    if model:
        cfg["model"] = model
    save_config(root, cfg)


def save_quality_mode(mode: str, app_dir: Path | None = None) -> None:
    root = app_dir or _app_dir()
    from engines.ai_manager.config import load_config, save_config
    from engines.translation_adapt import normalize_speed_mode

    cfg = load_config(root)
    cfg["quality_mode"] = normalize_speed_mode(mode)
    save_config(root, cfg)


def _prefer_speed_mode(quality_mode: str) -> bool:
    if str(quality_mode or "").lower() == "fast":
        return True
    env = str(os.getenv("VM_LLM_CPU_PREFER_SPEED") or "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return str(quality_mode or "").lower() == "fast"


def _pick_quality_model(available: list[str], *, prefer_speed: bool) -> str | None:
    if not available:
        return None
    try:
        from engines.llm_adaptation_mode import (
            _largest_model,
            _model_param_billions,
            _quality_floor_b,
            _smallest_model,
            _smallest_model_at_least,
        )
    except Exception:
        return available[0]

    if prefer_speed:
        return _smallest_model(available) or available[0]

    floor = _quality_floor_b()
    best = _smallest_model_at_least(available, floor)
    if best:
        return best
    return _largest_model(available) or available[0]


def _model_is_adequate(model_name: str) -> bool:
    try:
        from engines.llm_adaptation_mode import _model_param_billions, _quality_floor_b

        b = _model_param_billions(model_name)
        return 0.0 < b < 9999.0 and b >= _quality_floor_b()
    except Exception:
        return True


def resolve_model(
    available: list[str],
    *,
    provider: str = "",
    app_dir: Path | None = None,
    env_override: str | None = None,
) -> str:
    """Resolve the chat model tag for the active backend.

    Precedence:
      1. VM_TRANSLATE_MODEL env when valid
      2. Quality-aware pick from installed models (max_quality default)
      3. Persisted ai_module.json model when installed and adequate
      4. Persisted provider family → best installed tag
      5. Auto fallback chain: DeepSeek → Qwen → Llama → legacy heuristics
    """
    env_model = (env_override or os.getenv("VM_TRANSLATE_MODEL") or "").strip()
    if env_model and (not available or env_model in available):
        logger.info("[LLM] Loaded model: %s (env override)", env_model)
        return env_model

    persisted = load_persisted_selection(app_dir)
    quality_mode = str(persisted.get("quality_mode") or "max_quality")
    prefer_speed = _prefer_speed_mode(quality_mode)

    if available and not prefer_speed:
        quality_pick = _pick_quality_model(available, prefer_speed=False)
        if quality_pick:
            logger.info(
                "[LLM] Loaded model: %s (quality mode: %s)",
                quality_pick,
                quality_mode,
            )
            return quality_pick

    persisted_model = persisted.get("model") or ""
    if persisted_model and (not available or persisted_model in available):
        if prefer_speed or _model_is_adequate(persisted_model) or not available:
            logger.info("[LLM] Loaded model: %s (persisted)", persisted_model)
            return persisted_model
        upgraded = _pick_quality_model(available, prefer_speed=False)
        if upgraded and upgraded != persisted_model:
            logger.warning(
                "[LLM] Upgraded model: %s → %s (persisted model too small for quality)",
                persisted_model,
                upgraded,
            )
            return upgraded

    preferred_id = str(persisted.get("provider") or DEFAULT_FAMILY_ID).lower()
    order = _family_resolution_order(preferred_id)

    if prefer_speed and available:
        speed_pick = _pick_quality_model(available, prefer_speed=True)
        if speed_pick:
            logger.info("[LLM] Loaded model: %s (fast mode)", speed_pick)
            return speed_pick

    for fid in order:
        prov = get_provider(fid)
        if not prov:
            continue
        picked = prov.resolve_installed_model(available)
        if picked:
            if not prefer_speed and not _model_is_adequate(picked):
                continue
            if fid == preferred_id:
                logger.info("[LLM] Loaded model: %s (%s)", picked, prov.display_name)
            else:
                logger.warning(
                    "[LLM] Fallback model: %s (%s) — preferred %s not installed",
                    picked,
                    prov.display_name,
                    preferred_id,
                )
            return picked

    # Legacy auto-select (quality/speed heuristics in llm_adaptation_mode).
    try:
        from engines.llm_adaptation_mode import _legacy_resolve_llm_model

        legacy = _legacy_resolve_llm_model(available, provider=provider)
        if legacy:
            logger.info("[LLM] Loaded model: %s (legacy auto-select)", legacy)
            return legacy
    except Exception:
        pass

    if available:
        picked = _pick_quality_model(available, prefer_speed=prefer_speed)
        if picked:
            return picked

    fallback = env_model or persisted_model or DEEPSEEK.default_model
    logger.warning("[LLM] No local model matched — using configured default: %s", fallback)
    return fallback


def _family_resolution_order(preferred_id: str) -> tuple[str, ...]:
    pref = str(preferred_id or DEFAULT_FAMILY_ID).lower()
    rest = [fid for fid in FALLBACK_FAMILY_ORDER if fid != pref]
    if pref in _PROVIDERS:
        return (pref, *rest)
    return FALLBACK_FAMILY_ORDER


def resolve_provider_for_model(model_name: str) -> LLMProvider | None:
    for prov in list_providers():
        if prov.matches_installed(model_name):
            return prov
    return None
