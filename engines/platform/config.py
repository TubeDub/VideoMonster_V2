"""Feature flags and status for AI Media Platform modules (TZ Etap 10)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PLATFORM_FEATURE_MAP: dict[str, str] = {
    "live": "live_translation",
    "streaming": "live_stream",
    "broadcast_dub": "live_translation",
    "media_browser": "ai_studio",
    "recording": "voice_studio",
    "voice_training": "voice_trainer",
    "vocal_training": "singing_trainer",
    "assistant": "ai_studio",
}


def _flag(name: str, default: str = "0") -> bool:
    v = (os.getenv(name) or default).strip().lower()
    return v in ("1", "true", "yes", "on")


def _feature_enabled(feature_id: str) -> bool:
    from engines.feature_flags.manager import get_feature_manager

    app_dir = Path(__file__).resolve().parents[2]
    fm = get_feature_manager(app_dir)
    rec = fm.get(feature_id)
    if rec:
        return fm._effective_enabled(rec) and not rec.auto_disabled
    return False


def platform_master_enabled() -> bool:
    """Any platform module on via feature flags or legacy env."""
    if _flag("VM_PLATFORM_ENABLED"):
        return True
    return any(_feature_enabled(fid) for fid in set(PLATFORM_FEATURE_MAP.values()))


def _module_enabled(feature_id: str, env_name: str) -> bool:
    if _feature_enabled(feature_id):
        return True
    if _flag("VM_PLATFORM_ENABLED"):
        return True
    return _flag(env_name)


def live_translation_enabled() -> bool:
    return _module_enabled("live_translation", "VM_LIVE_TRANSLATION_ENABLED")


def streaming_studio_enabled() -> bool:
    return _module_enabled("live_stream", "VM_STREAMING_STUDIO_ENABLED")


def ai_live_dub_enabled() -> bool:
    return _module_enabled("live_translation", "VM_AI_LIVE_DUB_ENABLED")


def media_browser_enabled() -> bool:
    return _module_enabled("ai_studio", "VM_MEDIA_BROWSER_ENABLED")


def recording_studio_enabled() -> bool:
    return _module_enabled("voice_studio", "VM_RECORDING_STUDIO_ENABLED")


def voice_training_enabled() -> bool:
    return _module_enabled("voice_trainer", "VM_VOICE_TRAINING_ENABLED")


def vocal_training_enabled() -> bool:
    return _module_enabled("singing_trainer", "VM_VOCAL_TRAINING_ENABLED")


def ai_assistant_enabled() -> bool:
    return _module_enabled("ai_studio", "VM_AI_ASSISTANT_ENABLED")


def platform_diagnostics_enabled() -> bool:
    return _flag("VM_PLATFORM_DIAGNOSTICS", "1")


MODULE_FLAGS: dict[str, tuple[str, Any]] = {
    "live": ("VM_LIVE_TRANSLATION_ENABLED", live_translation_enabled),
    "streaming": ("VM_STREAMING_STUDIO_ENABLED", streaming_studio_enabled),
    "broadcast_dub": ("VM_AI_LIVE_DUB_ENABLED", ai_live_dub_enabled),
    "media_browser": ("VM_MEDIA_BROWSER_ENABLED", media_browser_enabled),
    "recording": ("VM_RECORDING_STUDIO_ENABLED", recording_studio_enabled),
    "voice_training": ("VM_VOICE_TRAINING_ENABLED", voice_training_enabled),
    "vocal_training": ("VM_VOCAL_TRAINING_ENABLED", vocal_training_enabled),
    "assistant": ("VM_AI_ASSISTANT_ENABLED", ai_assistant_enabled),
}


def require_module(module: str) -> None:
    """Check platform module via feature flags + release registry."""
    from engines.module_registry.registry import (
        get_registry,
        is_developer_session,
        module_accessible,
    )

    app_dir = Path(__file__).resolve().parents[2]
    reg = get_registry(app_dir)
    rec = None
    for m in reg.all_modules():
        if m.platform_key == module or m.id == module:
            rec = m
            break
    dev = is_developer_session()
    if rec and not module_accessible(
        rec,
        developer_mode=dev,
        show_beta=reg.show_beta_to_users(),
        user_mode="developer" if dev else "basic",
        app_dir=app_dir,
    ):
        raise PermissionError(
            f"Module '{module}' is not available (status={rec.status}). "
            "Enable feature flag or use Developer Mode on owner host."
        )
    fid = PLATFORM_FEATURE_MAP.get(module)
    if fid:
        from engines.feature_flags.manager import get_feature_manager

        fm = get_feature_manager(app_dir)
        if not fm.is_enabled(
            fid,
            user_mode="developer" if dev else "basic",
            developer_session=dev,
            show_beta=reg.show_beta_to_users(),
        ):
            rec = fm.get(fid)
            env = rec.env_key if rec else "FEATURE_*"
            raise PermissionError(
                f"Module '{module}' disabled ({env}=OFF). Enable in Developer Panel."
            )
        return
    entry = MODULE_FLAGS.get(module)
    if entry:
        _env, fn = entry
        if not fn() and not dev:
            raise PermissionError(
                f"Module '{module}' env disabled. Set {_env}=1 or enable feature flag."
            )


def platform_status() -> dict[str, Any]:
    return {
        "platform_enabled": platform_master_enabled(),
        "diagnostics": platform_diagnostics_enabled(),
        "modules": {name: fn() for name, (_env, fn) in MODULE_FLAGS.items()},
    }
