"""AI Core — Voice Director.

AI Core decides the voice delivery so the user never has to hand-tune it: from
the project profile (and per-segment emotion) it resolves emotional colour,
tempo, pitch, pauses, intonation and volume. Downstream TTS/prosody executors
consume these decisions.

Values are returned as neutral, engine-agnostic hints (multipliers / +N%
strings / labels) that existing prosody code already understands.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Tempo → speaking-rate percentage hint understood by the TTS/prosody layer.
_TEMPO_RATE = {
    "slow": "-8%",
    "medium": "+0%",
    "fast": "+8%",
}

# Emotion → (rate delta label, pitch label, intensity 0..1).
_EMOTION_DELIVERY = {
    "neutral":     {"rate": "+0%", "pitch": "+0%", "intensity": 0.3},
    "energetic":   {"rate": "+6%", "pitch": "+6%", "intensity": 0.8},
    "inquisitive": {"rate": "+0%", "pitch": "+4%", "intensity": 0.5},
    "somber":      {"rate": "-6%", "pitch": "-4%", "intensity": 0.6},
}


@dataclass
class VoiceDirection:
    emotion: str = "neutral"
    tempo: str = "medium"
    rate: str = "+0%"
    pitch: str = "+0%"
    volume: str = "+0%"
    intensity: float = 0.3
    pause_scale: float = 1.0     # multiplier on natural pause lengths
    intonation: str = "natural"  # natural/expressive/flat

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _combine_rate(tempo: str, emotion_rate: str) -> str:
    """Sum tempo + emotion rate deltas into one +N% string."""
    def _pct(v: str) -> int:
        try:
            return int(str(v).replace("%", "").replace("+", "").strip() or 0)
        except ValueError:
            return 0

    total = _pct(_TEMPO_RATE.get(tempo, "+0%")) + _pct(emotion_rate)
    total = max(-30, min(30, total))
    return f"{'+' if total >= 0 else ''}{total}%"


def decide_voice(profile, *, segment_emotion: str | None = None) -> VoiceDirection:
    """Resolve the voice delivery for a segment (or the project default).

    ``segment_emotion`` overrides the project's dominant emotion for a specific
    line when available (e.g. from the emotion tagger).
    """
    emotion = (segment_emotion or getattr(profile, "dominant_emotion", "neutral") or "neutral").lower()
    tempo = (getattr(profile, "tempo", "medium") or "medium").lower()
    delivery = _EMOTION_DELIVERY.get(emotion, _EMOTION_DELIVERY["neutral"])

    direction = VoiceDirection(
        emotion=emotion,
        tempo=tempo,
        rate=_combine_rate(tempo, delivery["rate"]),
        pitch=delivery["pitch"],
        volume="+0%",
        intensity=float(delivery["intensity"]),
    )

    # Pauses: keep dramatic content breathing; tighten fast talk formats.
    content_type = getattr(profile, "content_type", "movie")
    if content_type in {"movie", "tv_series", "audiobook"}:
        direction.pause_scale = 1.1
    elif content_type in {"blogger", "youtube", "anime"} or tempo == "fast":
        direction.pause_scale = 0.9
    else:
        direction.pause_scale = 1.0

    # Intonation follows emotional intensity.
    if direction.intensity >= 0.7:
        direction.intonation = "expressive"
    elif direction.intensity <= 0.2:
        direction.intonation = "flat"
    else:
        direction.intonation = "natural"

    return direction
