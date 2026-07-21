"""TTS stage — voice synthesis for segments."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from engines.streamdub.base import ModuleCapabilities, StreamModule
from engines.streamdub.types import StreamSegment

logger = logging.getLogger("tubedub.streamdub.tts")


class TTSEngine(StreamModule):
    module_id = "tts"

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        self._app_dir = Path(app_dir) if app_dir else None
        self._voice = str((config or {}).get("voice") or "uk-UA-OstapNeural")

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        try:
            from engines.tts import DEFAULT_VOICE

            return True, f"voice={self._voice or DEFAULT_VOICE}", None
        except Exception as exc:
            return False, str(exc), None

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            backends=["edge-offline", "edge-online"],
            features=["per_segment", "rate_control"],
        )

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        segments: list[StreamSegment] = list(payload.get("segments") or [])
        voice = str(payload.get("voice") or self._voice)
        tgt = str(payload.get("target_lang") or "uk")
        files: list[str] = []

        from engines.tts import generate_audio

        for seg in segments:
            text = (seg.translated or seg.text).strip()
            if not text:
                continue
            try:
                raw = generate_audio(text=text, voice=voice, segments=[text])
                if isinstance(raw, list) and raw:
                    files.append(str(raw[0]))
                    seg.meta["tts_file"] = str(raw[0])
            except Exception as exc:
                logger.warning("TTS seg %d failed: %s", seg.index, exc)
                seg.meta["tts_error"] = str(exc)

        return {"segments": segments, "tts_files": files, "voice": voice, "target_lang": tgt}
