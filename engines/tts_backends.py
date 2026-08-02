# -*- coding: utf-8 -*-
"""Stage 20 — Ukrainian TTS backends (edge / tts_uk / piper) unified factory."""

from __future__ import annotations

import contextvars
import logging
import os
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("tubedub.tts_backends")

# Per-task backend for estimate_tts_ms / closed_loop (thread-safe).
_pipeline_tts_backend: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pipeline_tts_backend", default=None
)


def set_pipeline_tts_backend(name: str | None) -> None:
    """Bind current pipeline TTS backend for duration estimates."""
    _pipeline_tts_backend.set(
        str(name).strip() if name else None
    )

# Canonical backend names (UI / settings)
TTS_BACKEND_EDGE = "edge"
TTS_BACKEND_TTS_UK = "tts_uk"
TTS_BACKEND_PIPER = "piper"

# Registry engine ids
ENGINE_EDGE = "edge-offline"
ENGINE_TTS_UK = "tts_uk"
ENGINE_PIPER = "piper"

TTS_UK_VOICES: dict[str, str] = {
    "lada": "female",
    "tetiana": "female",
    "mykyta": "male",
}
PIPER_UK_VOICES: dict[str, str] = {
    "uk_UA-lada-high": "female",
    "uk_UA-tetiana-high": "female",
    "uk_UA-mykyta-high": "male",
    "uk_UA-oleksa-high": "male",
}
EDGE_UK_VOICES: dict[str, str] = {
    "uk-UA-OstapNeural": "male",
    "uk-UA-PolinaNeural": "female",
}

DEFAULT_TTS_BACKEND = TTS_BACKEND_EDGE
DEFAULT_EDGE_VOICE = "uk-UA-OstapNeural"
DEFAULT_TTS_UK_VOICE = "mykyta"
DEFAULT_PIPER_VOICE = "uk_UA-mykyta-high"

_BACKEND_ALIASES = {
    "edge": ENGINE_EDGE,
    "edge-offline": ENGINE_EDGE,
    "edge-tts": ENGINE_EDGE,
    "edge_tts": ENGINE_EDGE,
    "tts_uk": ENGINE_TTS_UK,
    "tts-uk": ENGINE_TTS_UK,
    "ttsuk": ENGINE_TTS_UK,
    "piper": ENGINE_PIPER,
}


class BaseTTS(Protocol):
    """Stage 20 unified TTS surface (wraps registry engines)."""

    id: str

    def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
        **kwargs: Any,
    ) -> Any: ...

    def estimate_duration_ms(self, text: str, voice: str) -> int | None: ...

    def is_available(self) -> bool: ...


def normalize_backend_name(name: str | None) -> str:
    """Map UI/env aliases → registry engine id."""
    raw = str(name or "").strip().lower()
    if not raw:
        ctx = _pipeline_tts_backend.get()
        if ctx:
            raw = str(ctx).strip().lower()
        else:
            env = (
                os.getenv("TTS_BACKEND")
                or os.getenv("VM_TTS_ENGINE")
                or DEFAULT_TTS_BACKEND
            ).strip().lower()
            raw = env or DEFAULT_TTS_BACKEND
    return _BACKEND_ALIASES.get(raw, raw or ENGINE_EDGE)


def backend_display_name(engine_id: str | None) -> str:
    eid = normalize_backend_name(engine_id)
    if eid == ENGINE_TTS_UK:
        return TTS_BACKEND_TTS_UK
    if eid == ENGINE_PIPER:
        return TTS_BACKEND_PIPER
    return TTS_BACKEND_EDGE


def is_uk_tts_voice(voice: str) -> bool:
    """True for Edge uk-UA-*, tts_uk short names, Piper uk_UA-*."""
    v = str(voice or "").strip()
    if not v:
        return False
    if v.startswith("uk-UA-"):
        return True
    if v.startswith("uk_UA-") or v in PIPER_UK_VOICES:
        return True
    if v.lower() in TTS_UK_VOICES or v.lower() in {k.lower() for k in TTS_UK_VOICES}:
        return True
    if ":" in v:
        prefix, rest = v.split(":", 1)
        if prefix.lower() in ("tts_uk", "piper", "edge") and rest:
            return is_uk_tts_voice(rest)
    return False


def resolve_voice_for_backend(voice: str, backend: str | None) -> str:
    """Strip optional backend: prefix; default voice per backend when empty."""
    v = str(voice or "").strip()
    eid = normalize_backend_name(backend)
    if ":" in v:
        prefix, rest = v.split(":", 1)
        if prefix.lower() in ("tts_uk", "piper", "edge", "edge-offline"):
            v = rest.strip()
    if not v:
        if eid == ENGINE_TTS_UK:
            return DEFAULT_TTS_UK_VOICE
        if eid == ENGINE_PIPER:
            return DEFAULT_PIPER_VOICE
        return DEFAULT_EDGE_VOICE
    # Map Edge Ostap → mykyta when user switched backend but kept Edge voice id.
    if eid == ENGINE_TTS_UK and v.startswith("uk-UA-"):
        if "Ostap" in v or "ostap" in v.lower():
            return DEFAULT_TTS_UK_VOICE
        if "Polina" in v or "polina" in v.lower():
            return "tetiana"
        return DEFAULT_TTS_UK_VOICE
    if eid == ENGINE_PIPER and v.startswith("uk-UA-"):
        if "Ostap" in v or "ostap" in v.lower():
            return DEFAULT_PIPER_VOICE
        if "Polina" in v or "polina" in v.lower():
            return "uk_UA-tetiana-high"
        return DEFAULT_PIPER_VOICE
    if eid == ENGINE_TTS_UK:
        return v.lower() if v.lower() in TTS_UK_VOICES else v
    return v


def voices_for_backend(backend: str | None) -> list[dict[str, str]]:
    """UI voice list for a backend (uk)."""
    eid = normalize_backend_name(backend)
    if eid == ENGINE_TTS_UK:
        return [
            {"id": "mykyta", "name": "Микита (tts_uk, чол.) — рекомендований"},
            {"id": "lada", "name": "Лада (tts_uk, жін.)"},
            {"id": "tetiana", "name": "Тетяна (tts_uk, жін.)"},
        ]
    if eid == ENGINE_PIPER:
        return [
            {"id": "uk_UA-mykyta-high", "name": "Микита (Piper high, чол.)"},
            {"id": "uk_UA-oleksa-high", "name": "Олекса (Piper high, чол.)"},
            {"id": "uk_UA-lada-high", "name": "Лада (Piper high, жін.)"},
            {"id": "uk_UA-tetiana-high", "name": "Тетяна (Piper high, жін.)"},
        ]
    return [
        {"id": "uk-UA-OstapNeural", "name": "Остап (Edge, чол.) — за замовчуванням"},
        {"id": "uk-UA-PolinaNeural", "name": "Поліна (Edge, жін.)"},
    ]


def stamp_tts_backend_meta(
    seg: dict,
    *,
    engine_id: str | None,
    voice: str,
    sample_rate: int | None = None,
) -> None:
    """Write tts_backend / tts_voice / tts_sample_rate onto a segment."""
    eid = normalize_backend_name(engine_id)
    display = backend_display_name(eid)
    resolved = resolve_voice_for_backend(voice, eid)
    seg["tts_backend"] = display
    seg["tts_engine"] = eid
    seg["tts_voice"] = resolved
    if sample_rate:
        seg["tts_sample_rate"] = int(sample_rate)
    elif eid == ENGINE_TTS_UK:
        seg["tts_sample_rate"] = 44100
    elif eid == ENGINE_PIPER:
        seg.setdefault("tts_sample_rate", 22050)
    else:
        seg.setdefault("tts_sample_rate", 24000)


def estimate_duration_ms(
    text: str,
    voice: str = "",
    *,
    engine_id: str | None = None,
) -> int | None:
    """Optional pre-synth duration from backend (tts_uk); else None."""
    eid = normalize_backend_name(engine_id)
    try:
        eng = get_tts_backend(eid)
        fn = getattr(eng, "estimate_duration_ms", None)
        if callable(fn):
            ms = fn(text, resolve_voice_for_backend(voice, eid))
            if ms is not None and int(ms) > 0:
                return int(ms)
    except Exception as exc:
        logger.debug("estimate_duration_ms skipped: %s", exc)
    return None


def get_tts_backend(name: str | None = None):
    """Factory → registry engine (Edge / tts_uk / Piper) with Edge fallback."""
    from engines.tts_engines.registry import get_engine, synthesize as registry_synthesize

    eid = normalize_backend_name(name)
    eng = get_engine(eid)
    if not eng.is_available() and eid != ENGINE_EDGE:
        logger.warning(
            "[TTS] backend %s unavailable — fallback to edge-offline", eid
        )
        eng = get_engine(ENGINE_EDGE)
    return eng


def synthesize_with_backend(
    text: str,
    voice: str,
    output_path: str,
    *,
    engine_id: str | None = None,
    rate: str | None = None,
    pitch: str | None = None,
) -> Any:
    """Synthesize with normalize + voice resolve + Edge fallback."""
    from engines.tts_engines.registry import synthesize

    eid = normalize_backend_name(engine_id)
    resolved = resolve_voice_for_backend(voice, eid)
    result = synthesize(
        text,
        resolved,
        output_path,
        engine_id=eid,
        rate=rate,
        pitch=pitch,
    )
    if not result.ok and eid != ENGINE_EDGE:
        logger.warning(
            "[TTS] %s failed (%s) — fallback edge-offline",
            eid,
            (result.error or "")[:160],
        )
        edge_voice = (
            DEFAULT_EDGE_VOICE
            if not str(voice).startswith("uk-UA-")
            else voice
        )
        result = synthesize(
            text,
            edge_voice,
            output_path,
            engine_id=ENGINE_EDGE,
            rate=rate,
            pitch=pitch,
        )
    return result


def rate_to_length_scale(rate: str | None) -> float:
    """Edge-style '+10%' / '-5%' → Piper/tts_uk length_scale (~speaking rate inverse)."""
    raw = str(rate or "").strip()
    if not raw:
        return 1.0
    try:
        if raw.endswith("%"):
            pct = float(raw[:-1].replace("+", ""))
            # Faster rate (+10%) → shorter duration → length_scale < 1
            return max(0.5, min(2.0, 1.0 - (pct / 100.0)))
        return max(0.5, min(2.0, float(raw)))
    except (TypeError, ValueError):
        return 1.0


def ensure_wav_sample_rate(path: Path, target_sr: int = 24000) -> Path:
    """Resample WAV to target_sr when needed (pydub/ffmpeg)."""
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(str(path))
        if int(audio.frame_rate) == int(target_sr):
            return path
        audio = audio.set_frame_rate(int(target_sr))
        out = path.with_suffix(".wav")
        audio.export(str(out), format="wav")
        return out
    except Exception as exc:
        logger.debug("resample skipped: %s", exc)
        return path
