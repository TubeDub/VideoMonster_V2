"""Builtin lip-sync plugin — probes streamdub lip_sync module."""

from __future__ import annotations

from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_agent
from core.plugin_api import PluginHealth


class Plugin(BasePlugin):
    PLUGIN_NAME = "lip_sync"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["lip_sync"]

    def on_init(self) -> None:
        register_agent("lip_sync", self.process, plugin_name=self.PLUGIN_NAME)

    def health(self) -> PluginHealth:
        available = False
        detail = "module missing"
        try:
            from engines.streamdub.modules.lip_sync import LipSyncEngine  # noqa: F401

            available = True
            detail = "streamdub.lip_sync present"
        except Exception as exc:
            detail = str(exc)
        return PluginHealth(
            ok=self._initialized and available,
            message=detail,
            details={"available": available, "calls": self._call_count},
        )

    def process(self, payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        self.track_call()
        data = dict(payload or {})
        data.update(kwargs)
        try:
            from engines.streamdub.modules.lip_sync import LipSyncEngine

            engine = LipSyncEngine()
            try:
                engine.initialize(app_dir=self._context.get("app_dir"))
            except Exception:
                pass
            result = engine.process(data)
            return {"ok": True, "result": result, "engine": "lip_sync"}
        except Exception as exc:
            self.track_call(error=True)
            return {"ok": False, "error": str(exc), "engine": "lip_sync", "passthrough": True}


def create_plugin() -> Plugin:
    return Plugin()
