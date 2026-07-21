"""P601 — Universal VoiceProvider interface + engine adapters."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from engines.voice_platform.types import VoiceCapabilities, SynthesisResult

logger = logging.getLogger("tubedub.voice_platform.provider")


class VoiceProvider(ABC):
    """
    P601 — Dub Engine never knows concrete TTS.
    Every engine implements this contract.
    """

    provider_id: str = "base"
    display_name: str = "Base"

    def __init__(self) -> None:
        self._initialized = False

    @abstractmethod
    def initialize(self) -> bool:
        ...

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice_external_id: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
        emotion: str | None = None,
        language: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> SynthesisResult:
        ...

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        ...

    def shutdown(self) -> None:
        self._initialized = False

    @abstractmethod
    def capabilities(self) -> VoiceCapabilities:
        ...

    def supports_cloning(self) -> bool:
        return bool(self.capabilities().cloning)


class LegacyEngineAdapter(VoiceProvider):
    """Wrap existing BaseTTSEngine / provider adapters behind VoiceProvider."""

    def __init__(self, engine: Any) -> None:
        super().__init__()
        self._engine = engine
        self.provider_id = str(getattr(engine, "id", "unknown"))
        self.display_name = str(getattr(engine, "name", self.provider_id))

    def initialize(self) -> bool:
        ok = bool(getattr(self._engine, "is_available", lambda: False)())
        self._initialized = ok
        return ok

    def synthesize(
        self,
        text: str,
        voice_external_id: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
        emotion: str | None = None,
        language: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> SynthesisResult:
        if not self._initialized:
            self.initialize()
        t0 = time.perf_counter()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = self._engine.synthesize(
                text,
                voice_external_id,
                output_path,
                rate=rate,
                pitch=pitch,
            )
        except Exception as exc:
            return SynthesisResult(
                ok=False,
                provider=self.provider_id,
                error=str(exc),
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        ok = bool(getattr(raw, "ok", False))
        return SynthesisResult(
            ok=ok,
            output_path=str(getattr(raw, "output_path", output_path) or output_path),
            provider=self.provider_id,
            elapsed_ms=float(getattr(raw, "elapsed_ms", 0) or (time.perf_counter() - t0) * 1000),
            error=str(getattr(raw, "error", "") or ""),
            meta={
                "emotion": emotion,
                "language": language,
                "legacy": getattr(raw, "meta", None) or {},
                **(meta or {}),
            },
        )

    def health_check(self) -> dict[str, Any]:
        avail = bool(getattr(self._engine, "is_available", lambda: False)())
        return {
            "provider": self.provider_id,
            "ok": avail,
            "initialized": self._initialized,
            "mode": getattr(self._engine, "mode", ""),
        }

    def capabilities(self) -> VoiceCapabilities:
        eid = self.provider_id.lower()
        cloning = any(x in eid for x in ("xtts", "openvoice", "fish", "cosy", "clone"))
        return VoiceCapabilities(
            languages=[],
            cloning=cloning,
            prosody=bool(getattr(self._engine, "supports_ssml", False)),
            emotion=True,
            ssml=bool(getattr(self._engine, "supports_ssml", False)),
            streaming=False,
            offline="online" not in eid and "eleven" not in eid and "azure" not in eid,
        )


class MockVoiceProvider(VoiceProvider):
    """Deterministic silent WAV for tests / failover."""

    provider_id = "mock"
    display_name = "Mock Voice"

    def initialize(self) -> bool:
        self._initialized = True
        return True

    def synthesize(
        self,
        text: str,
        voice_external_id: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
        emotion: str | None = None,
        language: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> SynthesisResult:
        import wave

        t0 = time.perf_counter()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        sr = 16000
        # duration ~ proportional to text length (min 100ms)
        n_frames = max(sr // 10, min(sr * 3, len(text or " ") * 80))
        # Low-amplitude tone so quality validator does not flag pure silence
        import math
        import struct

        frames = bytearray()
        for i in range(n_frames):
            sample = int(1200 * math.sin(2 * math.pi * 220 * i / sr))
            frames.extend(struct.pack("<h", sample))
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(bytes(frames))
        return SynthesisResult(
            ok=True,
            output_path=str(path),
            provider=self.provider_id,
            voice_uuid=voice_external_id,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            meta={"emotion": emotion, "language": language, **(meta or {})},
        )

    def health_check(self) -> dict[str, Any]:
        return {"provider": self.provider_id, "ok": True, "initialized": self._initialized}

    def capabilities(self) -> VoiceCapabilities:
        return VoiceCapabilities(
            languages=["en", "ru", "uk"],
            cloning=False,
            prosody=True,
            emotion=True,
            offline=True,
        )
