"""Local neural TTS provider adapters — Freeze TZ P9.

Dub Engine talks only to the TTS API contract (BaseTTSEngine).
Providers: Coqui, Piper, XTTS, FishSpeech, CosyVoice, F5-TTS, GPT-SoVITS,
Chatterbox, OpenVoice (+ mock).

Real synthesis requires optional deps; adapters report availability and
return a clear error when the backend is not installed. Mock engine
always available for tests / CI. Edge remains the default Happy Path fallback.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Any

from engines.tts_engines.base import TTSResult

logger = logging.getLogger("tubedub.tts_engines.providers")


class _BaseAdapter:
    id: str = "base"
    name: str = "Base"
    mode: str = "offline"
    supports_stress: bool = False
    supports_ssml: bool = False
    _import_names: tuple[str, ...] = ()

    def is_available(self) -> bool:
        for name in self._import_names:
            try:
                __import__(name)
                return True
            except ImportError:
                continue
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
        if not self.is_available():
            return TTSResult(
                ok=False,
                engine_id=self.id,
                error=f"{self.name} backend not installed ({', '.join(self._import_names) or 'n/a'})",
            )
        return self._synthesize_impl(text, voice, output_path, rate=rate, pitch=pitch)

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult:
        return TTSResult(
            ok=False,
            engine_id=self.id,
            error=f"{self.name} synthesize not wired for this environment",
        )


class MockTTSEngine(_BaseAdapter):
    """Always-available silent WAV generator for tests / fallback."""

    id = "mock"
    name = "Mock TTS"
    mode = "offline"

    def is_available(self) -> bool:
        return True

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # ~100ms of silence @ 16kHz mono
        sr = 16000
        n_frames = sr // 10
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(b"\x00\x00" * n_frames)
        return TTSResult(
            ok=True,
            output_path=str(path),
            engine_id=self.id,
            meta={"voice": voice, "chars": len(text or ""), "mock": True},
        )


class CoquiTTSEngine(_BaseAdapter):
    id = "coqui"
    name = "Coqui TTS"
    _import_names = ("TTS", "TTS.api")

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult:
        import os

        try:
            from TTS.api import TTS  # type: ignore
        except ImportError:
            return TTSResult(ok=False, engine_id=self.id, error="TTS package not installed")

        model = (
            voice
            or os.getenv("VM_COQUI_MODEL")
            or "tts_models/multilingual/multi-dataset/xtts_v2"
        )
        # If voice is a wav path (clone API), treat as speaker reference not model id
        speaker_wav = ""
        if voice and Path(voice).is_file() and str(voice).lower().endswith(
            (".wav", ".mp3", ".flac", ".ogg")
        ):
            speaker_wav = voice
            model = (
                os.getenv("VM_COQUI_MODEL")
                or "tts_models/multilingual/multi-dataset/xtts_v2"
            )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tts = TTS(model_name=model, progress_bar=False)
            # XTTS needs speaker_wav; plain models use tts_to_file
            kwargs: dict[str, Any] = {"text": text, "file_path": str(path)}
            lang = (os.getenv("VM_COQUI_LANG") or "en").strip()
            if "xtts" in model.lower():
                speaker = speaker_wav or (os.getenv("VM_COQUI_SPEAKER_WAV") or "").strip()
                if not speaker or not Path(speaker).is_file():
                    return TTSResult(
                        ok=False,
                        engine_id=self.id,
                        error="speaker_wav / VM_COQUI_SPEAKER_WAV required for XTTS clone",
                    )
                kwargs["speaker_wav"] = speaker
                kwargs["language"] = lang
            tts.tts_to_file(**kwargs)
            if not path.is_file() or path.stat().st_size == 0:
                return TTSResult(ok=False, engine_id=self.id, error="empty_output")
            return TTSResult(
                ok=True,
                output_path=str(path),
                engine_id=self.id,
                meta={"cloning": bool(kwargs.get("speaker_wav")), "model": model},
            )
        except Exception as exc:  # noqa: BLE001
            return TTSResult(ok=False, engine_id=self.id, error=str(exc)[:300])


class PiperTTSEngine(_BaseAdapter):
    id = "piper"
    name = "Piper TTS"
    _import_names = ("piper", "piper_tts")

    def is_available(self) -> bool:
        import os
        import shutil

        if super().is_available():
            return True
        # CLI piper + model path (PIPER_MODEL / VM_PIPER_MODEL)
        if not shutil.which("piper"):
            return False
        model = (os.getenv("PIPER_MODEL") or os.getenv("VM_PIPER_MODEL") or "").strip()
        return bool(model and Path(model).is_file())

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult:
        import os
        import shutil
        import subprocess

        # Prefer Python piper package when present
        try:
            from piper import PiperVoice  # type: ignore

            model = (os.getenv("PIPER_MODEL") or os.getenv("VM_PIPER_MODEL") or voice or "").strip()
            if not model or not Path(model).is_file():
                return TTSResult(ok=False, engine_id=self.id, error="PIPER_MODEL not set")
            voice_obj = PiperVoice.load(model)
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as wf:
                voice_obj.synthesize(text, wf)
            return TTSResult(ok=True, output_path=str(path), engine_id=self.id)
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("piper package synthesize failed: %s", exc)

        piper_bin = shutil.which("piper")
        model = (os.getenv("PIPER_MODEL") or os.getenv("VM_PIPER_MODEL") or "").strip()
        if not piper_bin or not model:
            return TTSResult(
                ok=False,
                engine_id=self.id,
                error="piper CLI or PIPER_MODEL missing",
            )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # piper writes wav to stdout or -f
        try:
            proc = subprocess.run(
                [piper_bin, "--model", model, "--output_file", str(path)],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=120,
                check=False,
            )
            if proc.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
                err = (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
                return TTSResult(ok=False, engine_id=self.id, error=err or "piper_failed")
            return TTSResult(ok=True, output_path=str(path), engine_id=self.id)
        except Exception as exc:  # noqa: BLE001
            return TTSResult(ok=False, engine_id=self.id, error=str(exc)[:300])


class XTTSEngine(_BaseAdapter):
    """XTTS via Coqui TTS package — cloning path when speaker_wav provided."""

    id = "xtts"
    name = "XTTS"
    _import_names = ("TTS", "TTS.api")

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult:
        import os

        # Force XTTS model; voice may be a reference wav path from clone API
        coqui = CoquiTTSEngine()
        prev = os.environ.get("VM_COQUI_MODEL")
        os.environ["VM_COQUI_MODEL"] = (
            prev or "tts_models/multilingual/multi-dataset/xtts_v2"
        )
        try:
            result = coqui._synthesize_impl(text, voice, output_path, rate=rate, pitch=pitch)
            result.engine_id = self.id
            return result
        finally:
            if prev is None:
                os.environ.pop("VM_COQUI_MODEL", None)
            else:
                os.environ["VM_COQUI_MODEL"] = prev


class FishSpeechEngine(_BaseAdapter):
    id = "fishspeech"
    name = "Fish Speech"
    _import_names = ("fish_speech", "fishspeech")


class CosyVoiceEngine(_BaseAdapter):
    id = "cosyvoice"
    name = "CosyVoice"
    _import_names = ("cosyvoice",)


class F5TTSEngine(_BaseAdapter):
    """Local voice cloner stub (TZ Stage 5) — wire when F5-TTS deps are installed."""

    id = "f5-tts"
    name = "F5-TTS"
    _import_names = ("f5_tts", "f5tts")


class GPTSoVITSEngine(_BaseAdapter):
    """Local GPT-SoVITS stub (TZ Stage 5)."""

    id = "gpt-sovits"
    name = "GPT-SoVITS"
    _import_names = ("GPT_SoVITS", "gpt_sovits")


class ChatterboxEngine(_BaseAdapter):
    """Local Chatterbox stub (TZ Stage 5)."""

    id = "chatterbox"
    name = "Chatterbox"
    _import_names = ("chatterbox",)


class OpenVoiceEngine(_BaseAdapter):
    id = "openvoice"
    name = "OpenVoice"
    _import_names = ("openvoice",)


class ElevenLabsTTSEngine(_BaseAdapter):
    """Delegate to wired online ElevenLabs engine (HTTP, no SDK required)."""

    id = "elevenlabs"
    name = "ElevenLabs"
    mode = "online"
    _import_names = ("elevenlabs",)

    def is_available(self) -> bool:
        from engines.tts_engines.online_engines import ElevenLabsEngine

        return ElevenLabsEngine().is_available() or super().is_available()

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult:
        from engines.tts_engines.online_engines import ElevenLabsEngine

        return ElevenLabsEngine().synthesize(
            text, voice, output_path, rate=rate, pitch=pitch
        )


class EdgeTTSAdapter(_BaseAdapter):
    """Compatibility alias — real Edge lives in edge_engine; adapter probes availability."""

    id = "edge-tts"
    name = "Edge TTS Adapter"
    mode = "offline"

    def is_available(self) -> bool:
        try:
            from engines.tts_engines.edge_engine import EdgeTTSEngine

            return EdgeTTSEngine().is_available()
        except Exception:
            return False

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> TTSResult:
        from engines.tts_engines.edge_engine import EdgeTTSEngine

        return EdgeTTSEngine().synthesize(
            text, voice, output_path, rate=rate, pitch=pitch
        )


def provider_engines() -> list[Any]:
    return [
        MockTTSEngine(),
        CoquiTTSEngine(),
        PiperTTSEngine(),
        XTTSEngine(),
        FishSpeechEngine(),
        CosyVoiceEngine(),
        F5TTSEngine(),
        GPTSoVITSEngine(),
        ChatterboxEngine(),
        OpenVoiceEngine(),
        ElevenLabsTTSEngine(),
        EdgeTTSAdapter(),
    ]
