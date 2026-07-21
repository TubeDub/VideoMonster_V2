"""Whisper Engine — speech recognition with timestamps."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from engines.streamdub.base import HealthStatus, ModuleCapabilities, StreamModule
from engines.streamdub.types import StreamSegment

logger = logging.getLogger("tubedub.streamdub.whisper")


class WhisperEngine(StreamModule):
    module_id = "whisper"

    def __init__(self) -> None:
        super().__init__()
        self._app_dir: Path | None = None
        self._model_loaded = False

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        self._app_dir = Path(app_dir) if app_dir else None
        cfg = config or {}
        self._model_size = str(cfg.get("model_size") or "tiny")
        try:
            from engines.stt_engine import check_available

            ok, engine = check_available()
            self._model_loaded = ok
            self._engine_name = engine
        except Exception:
            self._model_loaded = False
            self._engine_name = ""

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        try:
            from engines.stt_engine import check_available

            ok, engine = check_available()
            return ok, engine or "unavailable", {"engine": engine}
        except Exception as exc:
            return False, str(exc), None

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            backends=["faster-whisper", "openai-whisper"],
            features=["timestamps", "pause_detection", "word_timestamps", "language_detection"],
        )

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        audio_path = str(payload.get("audio_path") or "")
        language = payload.get("source_lang")
        model_size = str(payload.get("model_size") or self._model_size)

        from engines.stt_engine import transcribe

        clean_text, _srt, timing_map, detected = transcribe(
            audio_path,
            language=language,
            model_size=model_size,
        )

        segments: list[StreamSegment] = []
        for i, row in enumerate(timing_map or []):
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            start = int(row.get("start") or row.get("start_ms") or 0)
            end = int(row.get("end") or row.get("end_ms") or start)
            if end < start:
                end = start + 1000
            pause = 0
            if i + 1 < len(timing_map):
                nxt = timing_map[i + 1]
                nstart = int(nxt.get("start") or nxt.get("start_ms") or end)
                pause = max(0, nstart - end)
            segments.append(
                StreamSegment(
                    index=len(segments),
                    text=text,
                    start_ms=start,
                    end_ms=end,
                    pause_after_ms=pause,
                    meta={"words": row.get("words") or []},
                )
            )

        return {
            "segments": segments,
            "clean_text": clean_text,
            "detected_lang": detected,
            "timing_map": timing_map,
        }
