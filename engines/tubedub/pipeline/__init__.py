"""Unified pipeline entry — all execution via PluginHost + ApiBus."""

from __future__ import annotations

from typing import Any


def run_unified_pipeline(info: dict[str, Any], *, app_dir: str = "", task_id: str = "") -> dict[str, Any]:
    """
    Single pipeline entry point.
    Stages execute as plugins; trace built via ApiBus (no direct module calls).
    """
    from engines.tubedub.api_bus import get_api_bus
    from engines.tubedub.module_manager import get_module_manager
    from pathlib import Path

    mgr = get_module_manager(Path(app_dir) if app_dir else Path("."))
    if not mgr.all_modules():
        mgr.bootstrap()

    resp = get_api_bus().call(
        "pipeline",
        "trace",
        {"info": info, "task_id": task_id, "app_dir": app_dir},
        caller="unified_pipeline",
    )
    if resp.ok:
        return {"ok": True, "view": resp.result.get("view")}
    return {"ok": False, "error": resp.error}


def list_pipeline_plugins() -> list[dict[str, Any]]:
    from engines.tubedub.plugin_host import PluginKind, get_plugin_host

    return get_plugin_host().list_plugins(kind=PluginKind.PIPELINE_STAGE.value)
