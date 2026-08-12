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


def get_pipeline_tts_backend() -> str | None:
    """Return currently bound pipeline TTS backend (if any)."""
    return _pipeline_tts_backend.get()

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

# Stage 23 — production Mykyta defaults (slightly slower / longer)
MYKYTA_RATE_DEFAULT = 0.97
MYKYTA_PITCH_DEFAULT = 0.0
MYKYTA_VOLUME_DEFAULT = 1.05
MYKYTA_LENGTH_SCALE_DEFAULT = 1.05
MYKYTA_RATE_RANGE = (0.85, 1.15)
MYKYTA_PITCH_RANGE = (-4.0, 4.0)
MYKYTA_VOLUME_RANGE = (0.7, 1.3)
# Duration-control may stretch length_scale up to 1.18
MYKYTA_LENGTH_SCALE_RANGE = (0.85, 1.18)
# Stage 23 duration-control clamps (slot fill via synth params)
MYKYTA_DURATION_LENGTH_SCALE_RANGE = (0.92, 1.18)
MYKYTA_DURATION_RATE_RANGE = (0.88, 1.08)

_pipeline_mykyta_controls: contextvars.ContextVar[dict[str, float] | None] = (
    contextvars.ContextVar("pipeline_mykyta_controls", default=None)
)


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


def _parse_mykyta_float(val: Any, default: float) -> float:
    """Parse Mykyta numeric control; ignore Edge-style rate/pitch strings."""
    if val is None:
        return float(default)
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return float(default)
    # Edge-style: "-5%", "+2Hz" — not Mykyta numeric; keep default.
    if s.endswith("%") or s.lower().endswith("hz"):
        return float(default)
    try:
        return float(s)
    except (TypeError, ValueError):
        return float(default)


def resolve_mykyta_controls(
    raw: dict[str, Any] | None = None,
    *,
    env: bool = True,
) -> dict[str, float]:
    """Resolve rate / pitch / volume / length_scale for tts_uk (Mykyta)."""
    src = dict(raw or {})
    ctx = _pipeline_mykyta_controls.get()
    if ctx:
        for k, v in ctx.items():
            src.setdefault(k, v)
    if env:
        for key, env_keys in (
            ("rate", ("MYKYTA_RATE", "VM_MYKYTA_RATE", "TTS_UK_RATE")),
            ("pitch", ("MYKYTA_PITCH", "VM_MYKYTA_PITCH", "TTS_UK_PITCH")),
            ("volume", ("MYKYTA_VOLUME", "VM_MYKYTA_VOLUME", "TTS_UK_VOLUME")),
            (
                "length_scale",
                ("MYKYTA_LENGTH_SCALE", "VM_MYKYTA_LENGTH_SCALE", "TTS_UK_LENGTH_SCALE"),
            ),
        ):
            if key in src and src[key] is not None:
                continue
            for ek in env_keys:
                ev = os.getenv(ek)
                if ev is not None and str(ev).strip() != "":
                    src[key] = ev
                    break
    rate = _clamp(
        _parse_mykyta_float(src.get("rate"), MYKYTA_RATE_DEFAULT),
        *MYKYTA_RATE_RANGE,
    )
    pitch = _clamp(
        _parse_mykyta_float(src.get("pitch"), MYKYTA_PITCH_DEFAULT),
        *MYKYTA_PITCH_RANGE,
    )
    volume = _clamp(
        _parse_mykyta_float(src.get("volume"), MYKYTA_VOLUME_DEFAULT),
        *MYKYTA_VOLUME_RANGE,
    )
    length_scale = _clamp(
        _parse_mykyta_float(src.get("length_scale"), MYKYTA_LENGTH_SCALE_DEFAULT),
        *MYKYTA_LENGTH_SCALE_RANGE,
    )
    return {
        "rate": rate,
        "pitch": pitch,
        "volume": volume,
        "length_scale": length_scale,
    }


def set_pipeline_mykyta_controls(controls: dict[str, Any] | None) -> None:
    """Bind Mykyta controls for the current pipeline task."""
    if not controls:
        _pipeline_mykyta_controls.set(None)
        return
    _pipeline_mykyta_controls.set(resolve_mykyta_controls(controls, env=False))


def compute_mykyta_duration_controls(
    slot_ms: int,
    measured_ms: int,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Stage 23: length_scale / rate to stretch short TTS toward the slot.

    length_scale = clamp(slot/measured, 0.92, 1.18)
    rate         = clamp(1/length_scale, 0.88, 1.08)
    """
    slot = max(1, int(slot_ms or 0))
    meas = max(1, int(measured_ms or 0))
    length_scale = _clamp(
        float(slot) / float(meas),
        *MYKYTA_DURATION_LENGTH_SCALE_RANGE,
    )
    rate = _clamp(
        1.0 / max(0.5, length_scale),
        *MYKYTA_DURATION_RATE_RANGE,
    )
    base_ctrl = resolve_mykyta_controls(base, env=False)
    return {
        "rate": rate,
        "pitch": base_ctrl["pitch"],
        "volume": base_ctrl["volume"],
        "length_scale": length_scale,
    }


def bind_pipeline_tts_from_info(info: dict[str, Any] | None) -> str:
    """Bind backend + Mykyta controls from task info; return engine id."""
    info = info or {}
    eid = normalize_backend_name(
        info.get("tts_engine") or info.get("tts_backend") or DEFAULT_TTS_BACKEND
    )
    set_pipeline_tts_backend(eid)
    if eid == ENGINE_TTS_UK:
        set_pipeline_mykyta_controls(
            {
                "rate": info.get("mykyta_rate", info.get("tts_rate")),
                "pitch": info.get("mykyta_pitch", info.get("tts_pitch")),
                "volume": info.get("mykyta_volume", info.get("tts_volume")),
                "length_scale": info.get(
                    "mykyta_length_scale", info.get("tts_length_scale")
                ),
            }
        )
    else:
        set_pipeline_mykyta_controls(None)
    return eid

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
    # Edge: map tts_uk / piper ids → Neural voices
    if eid == ENGINE_EDGE:
        vl = v.lower()
        if vl in TTS_UK_VOICES:
            return (
                DEFAULT_EDGE_VOICE
                if TTS_UK_VOICES[vl] == "male"
                else "uk-UA-PolinaNeural"
            )
        if v in PIPER_UK_VOICES or v.startswith("uk_UA-"):
            gender = PIPER_UK_VOICES.get(v, "male")
            return (
                DEFAULT_EDGE_VOICE
                if gender == "male"
                else "uk-UA-PolinaNeural"
            )
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
    controls: dict[str, Any] | None = None,
    language: str | None = None,
    cyrillic_ratio: float | None = None,
) -> None:
    """Write tts_backend / tts_voice / tts_language (+ Mykyta controls) onto a segment."""
    eid = normalize_backend_name(engine_id)
    display = backend_display_name(eid)
    lang = str(language or seg.get("tts_language") or seg.get("target_lang") or "uk")
    lang0 = lang.split("-")[0].lower()
    if lang0 == "uk":
        try:
            from engines.tts_lang_lock import force_uk_tts_identity

            ident = force_uk_tts_identity(
                target_lang="uk", engine_id=eid, voice=voice
            )
            eid = normalize_backend_name(ident.get("engine_id") or eid)
            display = backend_display_name(eid)
            resolved = str(ident.get("voice") or voice)
            lang0 = "uk"
        except Exception:
            resolved = resolve_voice_for_backend(voice, eid)
    else:
        resolved = resolve_voice_for_backend(voice, eid)
    seg["tts_backend"] = display
    seg["tts_engine"] = eid
    seg["tts_voice"] = resolved
    seg["tts_language"] = lang0
    seg["voice"] = resolved
    if cyrillic_ratio is not None:
        seg["cyrillic_ratio"] = float(cyrillic_ratio)
    elif "cyrillic_ratio" not in seg:
        try:
            from engines.tts_lang_lock import cyrillic_letter_ratio

            txt = str(
                seg.get("final_tts_text")
                or seg.get("tts_text")
                or seg.get("text")
                or ""
            )
            if txt.strip():
                seg["cyrillic_ratio"] = round(cyrillic_letter_ratio(txt), 3)
        except Exception:
            pass
    if sample_rate:
        seg["tts_sample_rate"] = int(sample_rate)
    elif eid == ENGINE_TTS_UK:
        seg["tts_sample_rate"] = 44100
    elif eid == ENGINE_PIPER:
        seg.setdefault("tts_sample_rate", 22050)
    else:
        seg.setdefault("tts_sample_rate", 24000)
    if eid == ENGINE_TTS_UK:
        ctrl = resolve_mykyta_controls(controls)
        seg["tts_rate"] = ctrl["rate"]
        seg["tts_pitch"] = ctrl["pitch"]
        seg["tts_volume"] = ctrl["volume"]
        seg["tts_length_scale"] = ctrl["length_scale"]


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
    volume: float | None = None,
    length_scale: float | None = None,
    mykyta_controls: dict[str, Any] | None = None,
) -> Any:
    """Synthesize with normalize + voice resolve + Edge fallback."""
    from engines.tts_engines.registry import synthesize

    eid = normalize_backend_name(engine_id)
    resolved = resolve_voice_for_backend(voice, eid)
    synth_kwargs: dict[str, Any] = {
        "engine_id": eid,
        "rate": rate,
        "pitch": pitch,
    }
    if eid == ENGINE_TTS_UK:
        ctrl = resolve_mykyta_controls(
            {
                **(mykyta_controls or {}),
                **({} if rate is None else {"rate": rate}),
                **({} if pitch is None else {"pitch": pitch}),
                **({} if volume is None else {"volume": volume}),
                **({} if length_scale is None else {"length_scale": length_scale}),
            }
        )
        # Prefer numeric Mykyta controls over Edge-style rate/pitch strings.
        synth_kwargs["rate"] = str(ctrl["rate"])
        synth_kwargs["pitch"] = str(ctrl["pitch"])
        synth_kwargs["volume"] = ctrl["volume"]
        synth_kwargs["length_scale"] = ctrl["length_scale"]
    # One retry on same backend before any fallback (Stage 24).
    result = synthesize(text, resolved, output_path, **synth_kwargs)
    if not result.ok and eid == ENGINE_TTS_UK:
        logger.warning(
            "[TTS] tts_uk retry once (%s)",
            (result.error or "")[:160],
        )
        result = synthesize(text, resolved, output_path, **synth_kwargs)
    if not result.ok and eid != ENGINE_EDGE:
        logger.warning(
            "[TTS] %s failed (%s) — fallback Edge uk-UA only",
            eid,
            (result.error or "")[:160],
        )
        try:
            from engines.tts_lang_lock import force_uk_tts_identity

            edge_ident = force_uk_tts_identity(
                target_lang="uk", engine_id=ENGINE_EDGE, voice=voice
            )
            edge_voice = str(edge_ident.get("voice") or DEFAULT_EDGE_VOICE)
        except Exception:
            edge_voice = DEFAULT_EDGE_VOICE
        # Never fall back to cs/sk/pl/ru — only uk-UA-*.
        if not str(edge_voice).startswith("uk-UA-"):
            edge_voice = DEFAULT_EDGE_VOICE
        # Edge rejects Mykyta float rates like "0.97"; convert to ±N%.
        edge_rate = rate
        try:
            raw = str(rate or "").strip()
            if raw and not raw.endswith("%") and not raw.startswith(("+", "-")):
                ratio = float(raw)
                if 0.5 <= ratio <= 2.0:
                    pct = int(round((ratio - 1.0) * 100))
                    edge_rate = f"{pct:+d}%"
        except Exception:
            edge_rate = "-4%"
        if not edge_rate or str(edge_rate).replace(".", "", 1).isdigit():
            edge_rate = "-4%"
        edge_pitch = pitch
        try:
            pr = str(pitch or "").strip()
            if pr and not pr.endswith("Hz") and not pr.startswith(("+", "-")):
                edge_pitch = None
        except Exception:
            edge_pitch = None
        result = synthesize(
            text,
            edge_voice,
            output_path,
            engine_id=ENGINE_EDGE,
            rate=edge_rate,
            pitch=edge_pitch,
        )
        if result.ok:
            try:
                meta = dict(result.meta or {})
                meta["edge_uk_fallback"] = True
                meta["edge_voice"] = edge_voice
                result.meta = meta
            except Exception:
                pass
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
