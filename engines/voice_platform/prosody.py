"""P609 Prosody Engine — stresses, rhythm, pauses, breath, intonation, speed."""

from __future__ import annotations

import re
from typing import Any

from engines.voice_platform.types import StyleProfile


def _estimate_stresses(text: str) -> list[dict[str, Any]]:
    words = re.findall(r"\w+", text or "", flags=re.UNICODE)
    out = []
    for i, w in enumerate(words):
        # Heuristic: longer words / content words get slight stress
        stress = 0.35
        if len(w) >= 6:
            stress = 0.7
        if i == 0:
            stress = max(stress, 0.55)
        if w[:1].isupper() and i > 0:
            stress = max(stress, 0.6)
        out.append({"word": w, "index": i, "stress": round(stress, 2)})
    return out


def _logical_pauses(text: str, pause_scale: float) -> list[dict[str, Any]]:
    pauses = []
    for m in re.finditer(r"[,;:—–\-]", text or ""):
        pauses.append({"at": m.start(), "ms": int(120 * pause_scale), "kind": "comma"})
    for m in re.finditer(r"[.!?…]", text or ""):
        pauses.append({"at": m.start(), "ms": int(220 * pause_scale), "kind": "sentence"})
    return pauses


def build_prosody_plan(
    text: str,
    *,
    style: StyleProfile | None = None,
    emotion: str = "calm",
) -> dict[str, Any]:
    """P609 — compute prosody before synthesis."""
    style = style or StyleProfile(name="Documentary")
    tempo = float(style.speech_rate)
    # Emotion tempo nudges
    emo_tempo = {
        "joy": 1.06,
        "anger": 1.1,
        "fear": 1.08,
        "sadness": 0.92,
        "surprise": 1.12,
        "irony": 1.0,
        "sarcasm": 0.98,
        "calm": 1.0,
    }
    tempo *= emo_tempo.get(emotion, 1.0)
    tempo = max(0.75, min(1.35, tempo))

    pitch_hz_bias = float(style.pitch_bias)
    emo_pitch = {
        "joy": 2.0,
        "anger": 1.5,
        "fear": 3.0,
        "sadness": -2.0,
        "surprise": 4.0,
        "calm": 0.0,
        "irony": 1.0,
        "sarcasm": -1.0,
    }
    pitch_hz_bias += emo_pitch.get(emotion, 0.0)

    # Edge-style rate/pitch strings
    rate_pct = int(round((tempo - 1.0) * 100))
    rate_str = f"{rate_pct:+d}%"
    pitch_str = f"{int(round(pitch_hz_bias)):+d}Hz"

    stresses = _estimate_stresses(text)
    pauses = _logical_pauses(text, style.pause_scale)
    breath = []
    if len(text or "") > 80:
        breath.append({"at": len(text) // 2, "ms": int(90 * style.pause_scale), "kind": "breath"})

    return {
        "tempo": round(tempo, 3),
        "rate_str": rate_str,
        "pitch_str": pitch_str,
        "pitch_bias": pitch_hz_bias,
        "stresses": stresses,
        "pauses": pauses,
        "breath": breath,
        "intonation": {
            "intensity": style.prosody_intensity,
            "diction": style.diction,
            "emotion": emotion,
        },
        "speed": tempo,
    }
