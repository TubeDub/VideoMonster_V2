"""Builtin ElevenLabs TTS plugin — optional cloud provider."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_tts
from core.plugin_api import PluginHealth


class Plugin(BasePlugin):
    PLUGIN_NAME = "elevenlabs"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["tts"]

    def on_init(self) -> None:
        register_tts("elevenlabs", self.synthesize, plugin_name=self.PLUGIN_NAME)

    def _api_key(self) -> str:
        return (os.getenv("ELEVENLABS_API_KEY") or os.getenv("VM_ELEVENLABS_API_KEY") or "").strip()

    def health(self) -> PluginHealth:
        configured = bool(self._api_key())
        return PluginHealth(
            ok=self._initialized and configured,
            message="ok" if configured else "ELEVENLABS_API_KEY not set",
            details={"configured": configured, "calls": self._call_count},
        )

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "Rachel",
        output_path: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.track_call()
        key = self._api_key()
        if not key:
            self.track_call(error=True)
            return {"ok": False, "error": "ELEVENLABS_API_KEY not set", "engine": "elevenlabs"}

        # Prefer existing voice platform / TTS registry if present
        try:
            from engines.tts_engines.registry import synthesize as registry_synthesize

            out = output_path or str(
                Path(self._context.get("app_dir") or ".")
                / "output"
                / "plugins"
                / "elevenlabs"
                / f"el_{abs(hash(text)) % 10_000_000}.mp3"
            )
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            result = registry_synthesize(
                text,
                voice,
                out,
                engine_id="elevenlabs",
            )
            ok = bool(getattr(result, "ok", False) or (isinstance(result, dict) and result.get("ok")))
            if not ok:
                self.track_call(error=True)
            if isinstance(result, dict):
                return result
            return {
                "ok": ok,
                "output_path": getattr(result, "output_path", out),
                "error": getattr(result, "error", None),
                "engine": "elevenlabs",
            }
        except Exception as exc:
            self.track_call(error=True)
            return {"ok": False, "error": str(exc), "engine": "elevenlabs"}


def create_plugin() -> Plugin:
    return Plugin()
