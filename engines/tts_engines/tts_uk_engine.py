# -*- coding: utf-8 -*-
"""Stage 20/22 — tts_uk (RAD-TTS++ + Vocos) backend adapter + Mykyta controls."""

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
        volume: float | None = None,
        length_scale: float | None = None,
        **kwargs,
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
            resolve_mykyta_controls,
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

        ctrl = resolve_mykyta_controls(
            {
                "rate": rate,
                "pitch": pitch,
                "volume": volume if volume is not None else kwargs.get("volume"),
                "length_scale": length_scale
                if length_scale is not None
                else kwargs.get("length_scale"),
            }
        )
        # token_dur_scaling: >1 lengthens; combine speaking rate + length_scale.
        # Faster rate → shorter duration → lower token_dur.
        token_dur = max(
            0.5,
            min(2.0, float(ctrl["length_scale"]) / max(0.5, float(ctrl["rate"]))),
        )
        # Pitch in semitones → mild f0_mean shift (~12 Hz per semitone).
        f0_mean = float(ctrl["pitch"]) * 12.0

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

        # Volume gain (pydub) when ≠ 1.0
        if abs(float(ctrl["volume"]) - 1.0) > 0.01:
            try:
                from pydub import AudioSegment

                audio = AudioSegment.from_wav(str(wav_path))
                # dB ≈ 20*log10(volume)
                import math

                db = 20.0 * math.log10(max(0.01, float(ctrl["volume"])))
                audio = audio.apply_gain(db)
                audio.export(str(wav_path), format="wav")
            except Exception as exc:
                logger.debug("tts_uk volume apply skipped: %s", exc)

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
                "tts_rate": ctrl["rate"],
                "tts_pitch": ctrl["pitch"],
                "tts_volume": ctrl["volume"],
                "tts_length_scale": ctrl["length_scale"],
                "token_dur_scaling": token_dur,
                "stats": stats if isinstance(stats, dict) else {},
                "duration_ms": dur_ms,
            },
        )
