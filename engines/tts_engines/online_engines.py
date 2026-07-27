"""Online commercial TTS engines — OpenAI / ElevenLabs / Azure / Google.

Replaces catalog stubs when the matching API key (or studio URL) is set.
Uses stdlib urllib only so no extra TTS SDKs are required.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from engines.tts_engines.base import TTSResult

logger = logging.getLogger("tubedub.tts_engines.online")


def _env(*names: str) -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return ""


def _write_bytes(path: str, data: bytes) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


def _http_post(
    url: str,
    *,
    headers: dict[str, str],
    body: bytes,
    timeout: float = 120.0,
) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _online_tts_enabled() -> bool:
    """Cloud TTS is opt-in so LLM API keys do not auto-enable TTS catalog entries."""
    return (os.getenv("VM_ENABLE_ONLINE_TTS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class _OnlineEngineBase:
    mode = "online"
    supports_stress = False
    supports_ssml = False
    id = "online"
    name = "Online TTS"
    provider = "cloud"

    def is_available(self) -> bool:
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
                error=(
                    f"{self.name}: not available "
                    "(set VM_ENABLE_ONLINE_TTS=1 and configure API key, or use edge-offline)"
                ),
            )
        clean = (text or "").strip()
        if not clean:
            return TTSResult(ok=False, engine_id=self.id, error="empty_text")
        t0 = time.perf_counter()
        try:
            self._synthesize_impl(clean, voice or "", output_path, rate=rate, pitch=pitch)
            if not Path(output_path).is_file() or Path(output_path).stat().st_size == 0:
                raise RuntimeError("empty_output")
            return TTSResult(
                ok=True,
                output_path=output_path,
                engine_id=self.id,
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] synthesize failed: %s", self.id, exc)
            return TTSResult(
                ok=False,
                engine_id=self.id,
                error=str(exc)[:400],
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            )

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> None:
        raise NotImplementedError


class OpenAIVoiceEngine(_OnlineEngineBase):
    id = "openai-voice"
    name = "OpenAI Voice"
    provider = "OpenAI"

    def is_available(self) -> bool:
        return _online_tts_enabled() and bool(_env("OPENAI_API_KEY", "VM_OPENAI_API_KEY"))

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> None:
        key = _env("OPENAI_API_KEY", "VM_OPENAI_API_KEY")
        model = _env("VM_OPENAI_TTS_MODEL") or "gpt-4o-mini-tts"
        # OpenAI voices: alloy, echo, fable, onyx, nova, shimmer, …
        voice_id = voice if voice and not voice.startswith(("ru-", "uk-", "en-")) else (
            _env("VM_OPENAI_TTS_VOICE") or "alloy"
        )
        payload = json.dumps(
            {"model": model, "input": text, "voice": voice_id, "response_format": "mp3"}
        ).encode("utf-8")
        data = _http_post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            body=payload,
        )
        _write_bytes(output_path, data)


class ElevenLabsEngine(_OnlineEngineBase):
    id = "elevenlabs"
    name = "ElevenLabs"
    provider = "ElevenLabs"
    supports_stress = True

    def is_available(self) -> bool:
        return _online_tts_enabled() and bool(
            _env("ELEVENLABS_API_KEY", "VM_ELEVENLABS_API_KEY")
        )

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> None:
        key = _env("ELEVENLABS_API_KEY", "VM_ELEVENLABS_API_KEY")
        voice_id = voice if voice and len(voice) > 8 and "-" not in voice[:3] else (
            _env("ELEVENLABS_VOICE_ID", "VM_ELEVENLABS_VOICE_ID") or "21m00Tcm4TlvDq8ikWAM"
        )
        model = _env("ELEVENLABS_MODEL", "VM_ELEVENLABS_MODEL") or "eleven_multilingual_v2"
        payload = json.dumps(
            {
                "text": text,
                "model_id": model,
                "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
            }
        ).encode("utf-8")
        data = _http_post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            body=payload,
        )
        _write_bytes(output_path, data)


class AzureNeuralEngine(_OnlineEngineBase):
    id = "azure-neural"
    name = "Azure Neural"
    provider = "Microsoft Azure"
    supports_stress = True
    supports_ssml = True

    def is_available(self) -> bool:
        return _online_tts_enabled() and bool(
            _env("AZURE_SPEECH_KEY", "VM_AZURE_SPEECH_KEY")
            and _env("AZURE_SPEECH_REGION", "VM_AZURE_SPEECH_REGION", "AZURE_REGION")
        )

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> None:
        key = _env("AZURE_SPEECH_KEY", "VM_AZURE_SPEECH_KEY")
        region = _env("AZURE_SPEECH_REGION", "VM_AZURE_SPEECH_REGION", "AZURE_REGION")
        voice_name = voice if voice and "-" in voice else (
            _env("AZURE_SPEECH_VOICE") or "en-US-JennyNeural"
        )
        rate_attr = f' rate="{rate}"' if rate else ""
        pitch_attr = f' pitch="{pitch}"' if pitch else ""
        ssml = (
            f'<speak version="1.0" xml:lang="{voice_name.split("-")[0]}-'
            f'{voice_name.split("-")[1] if "-" in voice_name else "US"}">'
            f'<voice name="{voice_name}"><prosody{rate_attr}{pitch_attr}>'
            f"{_xml_escape(text)}</prosody></voice></speak>"
        )
        data = _http_post(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "VideoMonsterV2",
            },
            body=ssml.encode("utf-8"),
        )
        _write_bytes(output_path, data)


class GoogleNeuralEngine(_OnlineEngineBase):
    id = "google-neural"
    name = "Google Neural"
    provider = "Google Cloud"
    supports_stress = True

    def is_available(self) -> bool:
        return _online_tts_enabled() and bool(
            _env("GOOGLE_TTS_KEY", "VM_GOOGLE_TTS_KEY", "GOOGLE_API_KEY")
        )

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> None:
        key = _env("GOOGLE_TTS_KEY", "VM_GOOGLE_TTS_KEY", "GOOGLE_API_KEY")
        # Accept "en-US-Neural2-A" style or fall back
        lang = "en-US"
        name = _env("GOOGLE_TTS_VOICE") or "en-US-Neural2-A"
        if voice and "-" in voice:
            parts = voice.split("-")
            if len(parts) >= 2:
                lang = f"{parts[0]}-{parts[1]}"
            name = voice
        speaking_rate = 1.0
        if rate:
            # Edge-style "+10%" / "-5%" → float
            try:
                speaking_rate = 1.0 + float(str(rate).replace("%", "").strip()) / 100.0
            except ValueError:
                speaking_rate = 1.0
        payload = json.dumps(
            {
                "input": {"text": text},
                "voice": {"languageCode": lang, "name": name},
                "audioConfig": {
                    "audioEncoding": "MP3",
                    "speakingRate": max(0.25, min(4.0, speaking_rate)),
                },
            }
        ).encode("utf-8")
        data = _http_post(
            f"https://texttospeech.googleapis.com/v1/text:synthesize?key={key}",
            headers={"Content-Type": "application/json"},
            body=payload,
        )
        parsed = json.loads(data.decode("utf-8"))
        audio_b64 = parsed.get("audioContent") or ""
        if not audio_b64:
            raise RuntimeError("Google TTS returned no audioContent")
        _write_bytes(output_path, base64.b64decode(audio_b64))


class OnlineStudioEngine(_OnlineEngineBase):
    """HTTP bridge to a self-hosted / TubeDub studio TTS endpoint."""

    id = "online-studio"
    name = "Online Studio"
    provider = "VideoMonster Cloud"

    def is_available(self) -> bool:
        return _online_tts_enabled() and bool(_env("VM_STUDIO_TTS_URL"))

    def _synthesize_impl(
        self,
        text: str,
        voice: str,
        output_path: str,
        *,
        rate: str | None = None,
        pitch: str | None = None,
    ) -> None:
        url = _env("VM_STUDIO_TTS_URL").rstrip("/")
        token = _env("VM_STUDIO_TTS_TOKEN", "VM_CLOUD_TOKEN")
        payload = json.dumps(
            {"text": text, "voice": voice, "rate": rate, "pitch": pitch}
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = _http_post(f"{url}/synthesize", headers=headers, body=payload)
        # Accept raw audio or JSON {audio_base64|url}
        if data[:1] == b"{":
            parsed = json.loads(data.decode("utf-8"))
            if parsed.get("audio_base64"):
                _write_bytes(output_path, base64.b64decode(parsed["audio_base64"]))
                return
            if parsed.get("url"):
                with urllib.request.urlopen(parsed["url"], timeout=120) as resp:
                    _write_bytes(output_path, resp.read())
                return
            raise RuntimeError("studio response missing audio")
        _write_bytes(output_path, data)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def online_engines() -> list:
    return [
        OnlineStudioEngine(),
        OpenAIVoiceEngine(),
        ElevenLabsEngine(),
        AzureNeuralEngine(),
        GoogleNeuralEngine(),
    ]
