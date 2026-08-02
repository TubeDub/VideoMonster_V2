# -*- coding: utf-8 -*-
"""Stage 20 — tts_uk (RAD-TTS++ + Vocos) backend adapter."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from engines.tts_engines.base import TTSResult

logger = logging.getLogger("tubedub.tts_engines.tts_uk")

_SAMPLE_RATE = 44100
_DOWNSTREAM_SR = int(os.getenv("VM_TTS_UK_OUT_SR") or "24000")


class TtsUkEngine:
    """High-quality Ukrainian TTS via ``tts-uk`` (optional dependency)."""

    id = "tts_uk"
    name = "tts_uk (RAD-TTS++ / Vocos)"
    mode = "offline"
    supports_stress = False
    supports_ssml = False

    def is_available(self) -> bool:
        try:
            from tts_uk.inference import synthesis  # noqa: F401

            return True
        except Exception:
            return False

    def estimate_duration_ms(self, text: str, voice: str = "mykyta") -> int | None:
        """Heuristic CPS estimate when package has no dry-run API."""
        t = " ".join(str(text or "").split()).strip()
        if not t:
            return 0
        # Comfortable UK CPS ~14 chars/sec (spaces excluded lightly).
        chars = len(t.replace(" ", ""))
        return max(200, int((chars / 14.0) * 1000.0))

    def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult:
        t0 = time.perf_counter()
        if not self.is_available():
            return TTSResult(
                ok=False,
                engine_id=self.id,
                error="tts-uk not installed (pip install tts-uk)",
            )
        from engines.tts_backends import (
            TTS_UK_VOICES,
            ensure_wav_sample_rate,
            rate_to_length_scale,
            resolve_voice_for_backend,
        )

        try:
            import torchaudio
            from tts_uk.inference import synthesis
        except Exception as exc:
            return TTSResult(
                ok=False,
                engine_id=self.id,
                error=f"tts_uk import failed: {exc}"[:300],
            )

        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return TTSResult(ok=False, engine_id=self.id, error="empty_text")

        v = resolve_voice_for_backend(voice, self.id).lower()
        if v not in TTS_UK_VOICES:
            v = "mykyta"
        length_scale = rate_to_length_scale(rate)
        # token_dur_scaling: >1 lengthens speech (inverse of Edge rate speedup)
        token_dur = max(0.5, min(2.0, 1.0 / max(0.5, length_scale)))
        # Optional pitch → mild f0_mean shift (Hz-ish; package accepts relative)
        f0_mean = 0.0
        if pitch:
            try:
                p = str(pitch).strip()
                if p.endswith("Hz"):
                    f0_mean = float(p.replace("Hz", "").replace("+", ""))
                elif p.endswith("%"):
                    f0_mean = float(p[:-1].replace("+", "")) * 0.5
            except (TypeError, ValueError):
                f0_mean = 0.0

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        wav_path = path.with_suffix(".wav")

        try:
            _mels, wave, stats = synthesis(
                text=clean,
                voice=v,
                n_takes=1,
                use_latest_take=False,
                token_dur_scaling=token_dur,
                f0_mean=f0_mean,
                f0_std=0,
                energy_mean=0,
                energy_std=0,
                sigma_decoder=0.8,
                sigma_token_duration=0.666,
                sigma_f0=1,
                sigma_energy=1,
            )
            torchaudio.save(
                str(wav_path),
                wave.cpu(),
                _SAMPLE_RATE,
                encoding="PCM_S",
            )
        except Exception as exc:
            logger.warning("tts_uk synthesis failed: %s", exc)
            return TTSResult(
                ok=False,
                engine_id=self.id,
                error=str(exc)[:300],
            )

        if not wav_path.is_file() or wav_path.stat().st_size == 0:
            return TTSResult(ok=False, engine_id=self.id, error="empty_output")

        # Downstream Edge path often expects mp3/24k — resample + optional convert.
        out = ensure_wav_sample_rate(wav_path, _DOWNSTREAM_SR)
        final = Path(output_path)
        if final.suffix.lower() == ".mp3" and out.suffix.lower() == ".wav":
            try:
                from pydub import AudioSegment

                AudioSegment.from_wav(str(out)).export(str(final), format="mp3")
                if out != wav_path and out.is_file():
                    try:
                        out.unlink()
                    except Exception:
                        pass
            except Exception:
                # Keep wav if mp3 export fails — rename/copy
                if out != final:
                    final = out if out.suffix == ".wav" else wav_path
        elif out != final:
            try:
                if final.exists():
                    final.unlink()
                out.replace(final)
            except Exception:
                final = out

        elapsed = (time.perf_counter() - t0) * 1000.0
        dur_ms = None
        if isinstance(stats, dict):
            for key in ("duration_ms", "duration", "audio_duration", "speech_duration"):
                if key in stats:
                    try:
                        val = float(stats[key])
                        dur_ms = int(val * 1000) if val < 1000 else int(val)
                        break
                    except (TypeError, ValueError):
                        pass
        return TTSResult(
            ok=True,
            output_path=str(final),
            engine_id=self.id,
            elapsed_ms=elapsed,
            meta={
                "voice": v,
                "tts_backend": "tts_uk",
                "tts_sample_rate": _SAMPLE_RATE,
                "token_dur_scaling": token_dur,
                "stats": stats if isinstance(stats, dict) else {},
                "duration_ms": dur_ms,
            },
        )
