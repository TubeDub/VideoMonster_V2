"""Builtin Whisper STT plugin — wraps engines.stt_engine."""

from __future__ import annotations

from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_stt


class Plugin(BasePlugin):
    PLUGIN_NAME = "whisper"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["stt"]

    def on_init(self) -> None:
        register_stt("whisper", self.transcribe, plugin_name=self.PLUGIN_NAME)

    def transcribe(self, audio_path: str, **kwargs: Any) -> dict[str, Any]:
        self.track_call()
        from engines.stt_engine import check_available, transcribe

        ok, msg = check_available()
        if not ok:
            self.track_call(error=True)
            return {"ok": False, "error": msg}

        language = kwargs.get("language")
        model_size = kwargs.get("model_size") or "tiny"
        text, srt, timing_map, detected = transcribe(
            str(audio_path),
            language=language,
            model_size=model_size,
        )
        return {
            "ok": True,
            "text": text,
            "srt": srt,
            "timing_map": timing_map,
            "detected": detected,
            "engine": "whisper",
        }


def create_plugin() -> Plugin:
    return Plugin()
