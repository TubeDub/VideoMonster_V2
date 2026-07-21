"""P610 Emotion Engine — map Semantic Core emotion → Voice Platform."""

from __future__ import annotations

from engines.voice_platform.types import EMOTION_ALIASES, EMOTIONS


def normalize_emotion(raw: str | None) -> str:
    if not raw:
        return "calm"
    key = str(raw).strip().lower()
    if key in EMOTIONS:
        return key
    return EMOTION_ALIASES.get(key, "calm")


def supported_emotions() -> tuple[str, ...]:
    return EMOTIONS


def emotion_tts_hints(emotion: str) -> dict[str, float | str]:
    """Hints for providers that accept rate/pitch deltas."""
    e = normalize_emotion(emotion)
    table = {
        "joy": {"rate_delta": 0.06, "pitch_delta": 2.0},
        "sadness": {"rate_delta": -0.08, "pitch_delta": -2.0},
        "fear": {"rate_delta": 0.08, "pitch_delta": 3.0},
        "surprise": {"rate_delta": 0.12, "pitch_delta": 4.0},
        "irony": {"rate_delta": 0.0, "pitch_delta": 1.0},
        "sarcasm": {"rate_delta": -0.02, "pitch_delta": -1.0},
        "calm": {"rate_delta": 0.0, "pitch_delta": 0.0},
        "anger": {"rate_delta": 0.1, "pitch_delta": 1.5},
    }
    hints = dict(table.get(e, table["calm"]))
    hints["emotion"] = e
    return hints
