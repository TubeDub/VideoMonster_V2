"""Edge-TTS — default offline neural engine."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from engines.tts_engines.base import TTSResult


class EdgeTTSEngine:
    id = "edge-offline"
    name = "Edge Neural (offline package)"
    mode = "offline"
    supports_stress = False
    supports_ssml = True

    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401

            return True
        except ImportError:
            return False

    def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult:
        import asyncio
        import os

        import edge_tts

        t0 = time.perf_counter()
        # UK Edge: light living rate (+4%) instead of flat 0%. Do not drive ±20%.
        effective_rate = (rate or os.getenv("VM_TTS_RATE") or "+0%").strip() or "+0%"
        voice_name = str(voice or "")
        if voice_name.startswith("uk-UA-") and effective_rate in ("+0%", "0%", "-0%", ""):
            effective_rate = "+4%"
        kwargs: dict = {"text": text, "voice": voice, "rate": effective_rate}
        effective_pitch = (pitch or "").strip() if pitch else ""
        if voice_name.startswith("uk-UA-") and str(text or "").rstrip().endswith("?"):
            try:
                from engines.simple_voice_lock import _bump_edge_pitch_hz

                effective_pitch = _bump_edge_pitch_hz(effective_pitch or "+0Hz", 2)
            except Exception:
                if not effective_pitch:
                    effective_pitch = "+2Hz"
        if effective_pitch:
            kwargs["pitch"] = effective_pitch
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(**kwargs)
                asyncio.run(asyncio.wait_for(communicate.save(output_path), timeout=120))
                if not Path(output_path).is_file() or Path(output_path).stat().st_size == 0:
                    raise RuntimeError("empty_output")
                ms = (time.perf_counter() - t0) * 1000.0
                return TTSResult(ok=True, output_path=output_path, engine_id=self.id, elapsed_ms=ms)
            except Exception as exc:
                last_err = exc
                if attempt < 2:
                    time.sleep(1.5)
        ms = (time.perf_counter() - t0) * 1000.0
        return TTSResult(ok=False, error=str(last_err or "TTS failed"), engine_id=self.id, elapsed_ms=ms)
