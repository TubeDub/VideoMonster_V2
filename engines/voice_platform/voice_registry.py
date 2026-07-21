"""P603 Voice Registry + P604 Voice Profiles."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from engines.voice_platform.types import StyleProfile, VoiceEntry

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_PROFILES: dict[str, StyleProfile] = {
    "Documentary": StyleProfile(
        name="Documentary",
        speech_rate=0.95,
        emotion_default="calm",
        diction="clear",
        pause_scale=1.15,
        prosody_intensity=0.35,
    ),
    "News": StyleProfile(
        name="News",
        speech_rate=1.08,
        emotion_default="calm",
        diction="energetic",
        pause_scale=0.85,
        prosody_intensity=0.55,
        pitch_bias=1.0,
    ),
    "Anime": StyleProfile(
        name="Anime",
        speech_rate=1.12,
        emotion_default="joy",
        diction="expressive",
        pause_scale=0.9,
        prosody_intensity=0.9,
        pitch_bias=2.0,
    ),
    "Movie": StyleProfile(
        name="Movie",
        speech_rate=1.0,
        emotion_default="calm",
        diction="soft",
        pause_scale=1.0,
        prosody_intensity=0.65,
    ),
    "Interview": StyleProfile(
        name="Interview",
        speech_rate=1.02,
        emotion_default="calm",
        diction="clear",
        pause_scale=1.05,
        prosody_intensity=0.45,
    ),
    "Podcast": StyleProfile(
        name="Podcast",
        speech_rate=1.05,
        emotion_default="calm",
        diction="soft",
        pause_scale=1.1,
        prosody_intensity=0.5,
    ),
}


def _stable_voice_uuid(provider: str, external_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"vm-voice:{provider}:{external_id}"))


class VoiceRegistry:
    """P603 — unified voice catalog."""

    def __init__(self) -> None:
        self._voices: dict[str, VoiceEntry] = {}
        self._by_external: dict[str, str] = {}  # external_id -> voice_uuid

    def register(self, entry: VoiceEntry) -> VoiceEntry:
        self._voices[entry.voice_uuid] = entry
        if entry.external_id:
            self._by_external[entry.external_id] = entry.voice_uuid
        return entry

    def get(self, voice_uuid: str) -> VoiceEntry | None:
        return self._voices.get(voice_uuid)

    def get_by_external(self, external_id: str) -> VoiceEntry | None:
        vu = self._by_external.get(external_id)
        return self._voices.get(vu) if vu else None

    def list_voices(
        self,
        *,
        language: str | None = None,
        provider: str | None = None,
    ) -> list[VoiceEntry]:
        rows = list(self._voices.values())
        if language:
            lang = language.lower()[:2]
            rows = [v for v in rows if v.language.lower().startswith(lang)]
        if provider:
            rows = [v for v in rows if v.provider == provider]
        return rows

    def to_dict(self) -> dict[str, Any]:
        return {"voices": [v.to_dict() for v in self._voices.values()]}


_REGISTRY: VoiceRegistry | None = None
_PROFILES: dict[str, StyleProfile] = dict(DEFAULT_PROFILES)


def load_voice_registry(*, refresh: bool = False) -> VoiceRegistry:
    global _REGISTRY
    if _REGISTRY is not None and not refresh:
        return _REGISTRY
    reg = VoiceRegistry()
    # From voice_catalog.json
    catalog_path = ROOT / "data" / "voice_catalog.json"
    if catalog_path.is_file():
        try:
            data = json.loads(catalog_path.read_text(encoding="utf-8"))
            for ext_id, meta in (data.get("voices") or {}).items():
                lang = "en"
                if ext_id.startswith("ru-"):
                    lang = "ru"
                elif ext_id.startswith("uk-"):
                    lang = "uk"
                elif ext_id.startswith("en-"):
                    lang = "en"
                entry = VoiceEntry(
                    voice_uuid=_stable_voice_uuid("edge-offline", ext_id),
                    provider="edge-offline",
                    language=lang,
                    gender=str(meta.get("gender") or "unknown"),
                    style="neutral",
                    display_name=str(meta.get("title") or ext_id),
                    external_id=ext_id,
                    quality="neural",
                    prosody_support=True,
                    license="microsoft-edge-tts",
                )
                reg.register(entry)
        except Exception:
            pass
    # From languages.VOICES
    try:
        from data.languages import VOICES

        for lang, voices in (VOICES or {}).items():
            for v in voices:
                ext = str(v.get("id") or "")
                if not ext or reg.get_by_external(ext):
                    continue
                reg.register(
                    VoiceEntry(
                        voice_uuid=_stable_voice_uuid("edge-offline", ext),
                        provider="edge-offline",
                        language=str(lang)[:2],
                        display_name=str(v.get("name") or ext),
                        external_id=ext,
                        quality="neural",
                        license="microsoft-edge-tts",
                    )
                )
    except Exception:
        pass
    # Mock voice for tests / failover
    reg.register(
        VoiceEntry(
            voice_uuid=_stable_voice_uuid("mock", "mock-default"),
            provider="mock",
            language="mul",
            gender="neutral",
            display_name="Mock Default",
            external_id="mock-default",
            quality="silent",
            cloning_support=False,
            license="internal",
        )
    )
    # Optional custom profiles file
    profiles_path = ROOT / "data" / "voice_profiles.json"
    if profiles_path.is_file():
        try:
            pdata = json.loads(profiles_path.read_text(encoding="utf-8"))
            for name, cfg in (pdata.get("profiles") or pdata or {}).items():
                if not isinstance(cfg, dict):
                    continue
                _PROFILES[name] = StyleProfile(
                    name=name,
                    speech_rate=float(cfg.get("speech_rate", 1.0)),
                    emotion_default=str(cfg.get("emotion_default", "calm")),
                    pitch_bias=float(cfg.get("pitch_bias", 0.0)),
                    diction=str(cfg.get("diction", "neutral")),
                    pause_scale=float(cfg.get("pause_scale", 1.0)),
                    prosody_intensity=float(cfg.get("prosody_intensity", 0.5)),
                )
        except Exception:
            pass
    _REGISTRY = reg
    return reg


def get_style_profile(name: str) -> StyleProfile:
    load_voice_registry()
    return _PROFILES.get(name) or _PROFILES.get("Documentary") or DEFAULT_PROFILES["Documentary"]


def list_style_profiles() -> list[StyleProfile]:
    load_voice_registry()
    return list(_PROFILES.values())


def resolve_voice(
    *,
    voice_uuid: str | None = None,
    external_id: str | None = None,
    language: str | None = None,
    provider: str | None = None,
) -> VoiceEntry:
    reg = load_voice_registry()
    if voice_uuid:
        v = reg.get(voice_uuid)
        if v:
            return v
    if external_id:
        v = reg.get_by_external(external_id)
        if v:
            return v
    candidates = reg.list_voices(language=language, provider=provider)
    if candidates:
        return candidates[0]
    mock = reg.get_by_external("mock-default")
    if mock:
        return mock
    raise LookupError("No voices registered")
