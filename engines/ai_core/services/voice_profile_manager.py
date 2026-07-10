"""Voice Profile Manager (TZ Stage 5) — single source for TTS voice parameters."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.voice_profile_manager")

_APP_DIR = Path(__file__).resolve().parents[3]
_PROFILES_PATH = _APP_DIR / "data" / "voice_profiles.json"

# Default speech-rate heuristics (chars/sec) — aligned with timing predictor.
_CHARS_PER_SEC: dict[str, float] = {
    "ru": 14.0,
    "uk": 13.5,
    "en": 13.0,
    "de": 12.5,
    "default": 13.5,
}


def _load_custom_profiles() -> dict[str, Any]:
    if not _PROFILES_PATH.is_file():
        return {}
    try:
        data = json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _catalog_voices() -> dict[str, list[dict[str, str]]]:
    try:
        from data.languages import VOICES

        return dict(VOICES)
    except Exception:
        return {}


def _default_profile(voice_id: str, lang: str) -> dict[str, Any]:
    base = normalize_lang(lang)
    cps = _CHARS_PER_SEC.get(base, _CHARS_PER_SEC["default"])
    return {
        "voice_id": voice_id,
        "language": base,
        "average_speech_rate": round(cps, 2),
        "density": 1.0,
        "pause_gap_ms": 80,
        "confidence": 0.85,
        "voice_engine": "edge-tts",
        "voice_version": "1.0",
        "source": "default",
    }


class VoiceProfileManager:
    """Resolve TTS voice parameters — no storage inside Quality Gate."""

    def __init__(self) -> None:
        self._custom = _load_custom_profiles()

    def reload(self) -> None:
        self._custom = _load_custom_profiles()

    def list_voices(self, lang: str) -> list[dict[str, str]]:
        base = normalize_lang(lang)
        catalog = _catalog_voices()
        for key in (base, lang, f"{base}-CN" if base == "zh" else base):
            voices = catalog.get(key)
            if voices:
                return list(voices)
        return catalog.get("en", [])

    def get_profile(self, voice_id: str, lang: str = "") -> dict[str, Any]:
        vid = str(voice_id or "").strip()
        custom = self._custom.get(vid) or self._custom.get(f"{normalize_lang(lang)}:{vid}")
        if isinstance(custom, dict):
            return {**_default_profile(vid, lang or custom.get("language", "en")), **custom, "source": "custom"}

        # Infer language from edge-tts voice id (e.g. uk-UA-PolinaNeural)
        inferred = lang
        if not inferred and "-" in vid:
            inferred = vid.split("-", 1)[0]
        return _default_profile(vid, inferred or "en")

    def resolve_for_task(self, task_info: dict[str, Any]) -> dict[str, Any]:
        voice_id = str(
            task_info.get("voice")
            or task_info.get("tts_voice")
            or task_info.get("selected_voice")
            or ""
        )
        lang = str(task_info.get("target_lang") or task_info.get("tgt_lang") or "ru")
        if not voice_id:
            voices = self.list_voices(lang)
            voice_id = voices[0]["id"] if voices else ""
        return self.get_profile(voice_id, lang)

    def resolve_for_segment(
        self,
        segment: dict[str, Any],
        task_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        brief = segment.get("creative_brief") or {}
        task = task_info or {}
        voice_id = str(
            brief.get("speaker_id")
            or task.get("voice")
            or task.get("tts_voice")
            or ""
        )
        lang = str(task.get("target_lang") or segment.get("tgt_lang") or "ru")
        profile = self.get_profile(voice_id, lang)
        speed = brief.get("speaking_speed")
        if speed is not None:
            try:
                profile = {**profile, "density": float(speed)}
            except (TypeError, ValueError):
                pass
        return profile


_MANAGER: VoiceProfileManager | None = None


def get_voice_profile_manager() -> VoiceProfileManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = VoiceProfileManager()
    return _MANAGER
