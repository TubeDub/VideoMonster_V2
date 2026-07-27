"""Builtin voice clone plugin — wraps engines.voice_platform.cloning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_tts
from core.plugin_api import PluginHealth


class Plugin(BasePlugin):
    PLUGIN_NAME = "voice_clone"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["voice_clone", "tts"]

    def on_init(self) -> None:
        register_tts("voice_clone", self.clone_synthesize, plugin_name=self.PLUGIN_NAME)

    def health(self) -> PluginHealth:
        try:
            from engines.voice_platform.cloning import clone_readiness

            ready = clone_readiness()
            available = bool(ready.get("available"))
            return PluginHealth(
                ok=self._initialized and available,
                message="ok" if available else (ready.get("message") or "clone unavailable"),
                details={
                    "adapter": ready.get("adapter_id"),
                    "required_engines": ready.get("required_engines") or [],
                    "missing_engines": ready.get("missing_engines") or [],
                    "calls": self._call_count,
                },
            )
        except Exception as exc:
            return PluginHealth(ok=False, message=str(exc))

    def clone_synthesize(
        self,
        text: str,
        *,
        reference_wav: str = "",
        output_path: str = "",
        language: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.track_call()
        from engines.voice_platform.cloning import clone_voice

        if not reference_wav:
            self.track_call(error=True)
            return {"ok": False, "error": "reference_wav required"}
        out = output_path or str(
            Path(self._context.get("app_dir") or ".")
            / "output"
            / "plugins"
            / "voice_clone"
            / f"clone_{abs(hash(text + reference_wav)) % 10_000_000}.wav"
        )
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        result = clone_voice(text, reference_wav, out, language=language)
        payload = {
            "ok": bool(getattr(result, "ok", False)),
            "output_path": getattr(result, "output_path", out),
            "error": getattr(result, "error", None),
            "provider": getattr(result, "provider", None),
        }
        if not payload["ok"]:
            self.track_call(error=True)
        return payload


def create_plugin() -> Plugin:
    return Plugin()
