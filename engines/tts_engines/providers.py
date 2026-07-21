"""Local neural TTS provider adapters — Freeze TZ P9.

Dub Engine talks only to the TTS API contract (BaseTTSEngine).
Providers: Coqui, Piper, XTTS, FishSpeech, CosyVoice, OpenVoice (+ mock).

Real synthesis requires optional deps; adapters report availability and
raise a clear TTSError when the backend is not installed. Mock engine
always available for tests / CI.
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


class PiperTTSEngine(_BaseAdapter):
    id = "piper"
    name = "Piper TTS"
    _import_names = ("piper", "piper_tts")


class XTTSEngine(_BaseAdapter):
    id = "xtts"
    name = "XTTS"
    _import_names = ("TTS", "TTS.api")


class FishSpeechEngine(_BaseAdapter):
    id = "fishspeech"
    name = "Fish Speech"
    _import_names = ("fish_speech", "fishspeech")


class CosyVoiceEngine(_BaseAdapter):
    id = "cosyvoice"
    name = "CosyVoice"
    _import_names = ("cosyvoice",)


class OpenVoiceEngine(_BaseAdapter):
    id = "openvoice"
    name = "OpenVoice"
    _import_names = ("openvoice",)


class ElevenLabsTTSEngine(_BaseAdapter):
    id = "elevenlabs"
    name = "ElevenLabs"
    mode = "online"
    _import_names = ("elevenlabs",)


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
        OpenVoiceEngine(),
        ElevenLabsTTSEngine(),
        EdgeTTSAdapter(),
    ]
