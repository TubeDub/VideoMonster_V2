"""VideoMonster V2 Developer SDK — core registration API (TZ #9 §11).

Plugin developers use these functions without importing internal core modules::

    from sdk.core_api import register_translation, register_tts

All registrations go through PluginManager — the core itself is never modified.
"""

from __future__ import annotations

from typing import Any, Callable

from core.plugin_api import PluginManifest
from core.plugin_manager import get_plugin_manager


def _mgr():
    return get_plugin_manager()


def register_plugin(name: str, instance: Any, *, manifest: dict[str, Any] | None = None) -> None:
    """Register a plugin instance programmatically."""
    mf = None
    if manifest:
        mf = PluginManifest(
            name=str(manifest.get("name", name)),
            version=str(manifest.get("version", "0.1.0")),
            capabilities=list(manifest.get("capabilities") or []),
            dependencies=list(manifest.get("dependencies") or []),
        )
    _mgr().register_plugin_instance(name, instance, mf)


def register_agent(name: str, handler: Callable[..., Any], *, plugin_name: str = "sdk") -> None:
    _mgr().register_capability_handler("agents", name, handler, plugin_name=plugin_name)


def register_model(name: str, descriptor: Any, *, plugin_name: str = "sdk") -> None:
    _mgr().register_capability_handler("models", name, descriptor, plugin_name=plugin_name)
    try:
        from core.model_registry import get_registry
        if hasattr(descriptor, "name"):
            get_registry().register(descriptor)
    except Exception:
        pass


def register_exporter(name: str, handler: Callable[..., Any], *, plugin_name: str = "sdk") -> None:
    _mgr().register_capability_handler("exporters", name, handler, plugin_name=plugin_name)


def register_tts(name: str, handler: Callable[..., Any], *, plugin_name: str = "sdk") -> None:
    _mgr().register_capability_handler("tts", name, handler, plugin_name=plugin_name)


def register_stt(name: str, handler: Callable[..., Any], *, plugin_name: str = "sdk") -> None:
    _mgr().register_capability_handler("stt", name, handler, plugin_name=plugin_name)


def register_translation(name: str, handler: Callable[..., Any], *, plugin_name: str = "sdk") -> None:
    _mgr().register_capability_handler("translation", name, handler, plugin_name=plugin_name)


def register_review(name: str, handler: Callable[..., Any], *, plugin_name: str = "sdk") -> None:
    _mgr().register_capability_handler("review", name, handler, plugin_name=plugin_name)


def register_event(event_type: str, handler: Callable[..., Any], *, plugin_name: str = "sdk") -> None:
    """Register an event handler (stored in plugin registry for orchestrator integration)."""
    _mgr().register_capability_handler("events", event_type, handler, plugin_name=plugin_name)


def register_memory_provider(name: str, handler: Callable[..., Any], *, plugin_name: str = "sdk") -> None:
    _mgr().register_capability_handler("memory_providers", name, handler, plugin_name=plugin_name)


def list_registrations(category: str = "") -> dict[str, Any]:
    mgr = _mgr()
    if category:
        return dict(mgr._registries.get(category, {}))
    return {k: list(v.keys()) for k, v in mgr._registries.items()}
