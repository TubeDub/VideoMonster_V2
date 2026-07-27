"""Builtin Edge-TTS plugin — wraps engines.tts_engines.edge_engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_tts


class Plugin(BasePlugin):
    PLUGIN_NAME = "edge_tts"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["tts"]

    def on_init(self) -> None:
        register_tts("edge_tts", self.synthesize, plugin_name=self.PLUGIN_NAME)

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "ru-RU-DmitryNeural",
        output_path: str = "",
        rate: str | None = None,
        pitch: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.track_call()
        from engines.tts_engines.edge_engine import EdgeTTSEngine

        engine = EdgeTTSEngine()
        if not engine.is_available():
            self.track_call(error=True)
            return {"ok": False, "error": "edge_tts package not installed", "engine": engine.id}

        out = output_path or str(
            Path(self._context.get("app_dir") or ".")
            / "output"
            / "plugins"
            / "edge_tts"
            / f"tts_{abs(hash(text)) % 10_000_000}.mp3"
        )
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        result = engine.synthesize(text, voice, out, rate=rate, pitch=pitch)
        payload = {
            "ok": result.ok,
            "output_path": result.output_path,
            "engine": result.engine_id,
            "elapsed_ms": result.elapsed_ms,
            "error": result.error,
        }
        if not result.ok:
            self.track_call(error=True)
        return payload


def create_plugin() -> Plugin:
    return Plugin()
