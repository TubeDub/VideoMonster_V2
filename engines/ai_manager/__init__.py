"""TubeDub AI Manager."""

from engines.ai_manager.manager import (
    build_openddf_ai_installation,
    defer_install,
    download_provider_model,
    is_ai_ready,
    prompt_needed,
    reconcile_install_state,
    select_model,
    select_quality_mode,
    start_install,
    uninstall,
    clear_model_cache,
    user_status,
)

__all__ = [
    "build_openddf_ai_installation",
    "defer_install",
    "download_provider_model",
    "is_ai_ready",
    "prompt_needed",
    "reconcile_install_state",
    "select_model",
    "select_quality_mode",
    "start_install",
    "uninstall",
    "clear_model_cache",
    "user_status",
]
