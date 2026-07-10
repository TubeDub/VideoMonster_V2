"""Placeholder engines for future commercial integrations."""

from __future__ import annotations

from engines.tts_engines.base import TTSResult


class _StubEngine:
    mode = "online"
    supports_stress = False
    supports_ssml = False

    def __init__(self, engine_id: str, name: str, provider: str, *, stress: bool = False):
        self.id = engine_id
        self.name = name
        self.provider = provider
        self.supports_stress = stress

    def is_available(self) -> bool:
        import os

        key_map = {
            "openai-voice": "OPENAI_API_KEY",
            "elevenlabs": "ELEVENLABS_API_KEY",
            "azure-neural": "AZURE_SPEECH_KEY",
            "google-neural": "GOOGLE_TTS_KEY",
            "online-studio": "VM_STUDIO_TTS_URL",
        }
        env_key = key_map.get(self.id)
        if not env_key:
            return False
        return bool((os.getenv(env_key) or "").strip())

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
                error=f"{self.name}: API key or endpoint not configured",
                engine_id=self.id,
            )
        return TTSResult(
            ok=False,
            error=f"{self.name}: integration pending — use edge-offline",
            engine_id=self.id,
        )


def stub_engines() -> list:
    return [
        _StubEngine("online-studio", "Online Studio", "VideoMonster Cloud"),
        _StubEngine("openai-voice", "OpenAI Voice", "OpenAI"),
        _StubEngine("elevenlabs", "ElevenLabs", "ElevenLabs", stress=True),
        _StubEngine("azure-neural", "Azure Neural", "Microsoft Azure", stress=True),
        _StubEngine("google-neural", "Google Neural", "Google Cloud", stress=True),
    ]
