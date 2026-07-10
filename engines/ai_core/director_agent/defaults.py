"""Safe default CreativeBrief field values."""

from __future__ import annotations

from typing import Any

DEFAULT_EMOTION = "Neutral"
DEFAULT_SPEECH_STYLE = "conversational"
DEFAULT_SPEAKING_SPEED = "normal"
DEFAULT_UTTERANCE_GOAL = "inform"

DEFAULT_BRIEF_VALUES: dict[str, Any] = {
    "emotion": DEFAULT_EMOTION,
    "speech_style": DEFAULT_SPEECH_STYLE,
    "speaking_speed": DEFAULT_SPEAKING_SPEED,
    "formality": 0.5,
    "humor": 0.0,
    "sarcasm": 0.0,
    "aggression": 0.0,
    "calmness": 0.5,
    "emotional_intensity": 0.3,
    "allowed_compression": 0.35,
    "allowed_expansion": 0.25,
    "adaptation_priority": 0.6,
    "meaning_priority": 0.95,
    "lip_sync_priority": 0.7,
    "naturalness_priority": 0.75,
    "utterance_goal": DEFAULT_UTTERANCE_GOAL,
    "literal_phrasing_importance": 0.4,
    "deep_semantic_adaptation_needed": True,
}

VALID_EMOTIONS = frozenset(
    {"Neutral", "Happy", "Sad", "Angry", "Fear", "Excited", "Calm"}
)
VALID_SPEECH_STYLES = frozenset(
    {"conversational", "formal", "dramatic", "narrative"}
)
VALID_SPEAKING_SPEEDS = frozenset({"slow", "normal", "fast"})
VALID_UTTERANCE_GOALS = frozenset({"inform", "question", "command", "exclaim"})

__all__ = [
    "DEFAULT_BRIEF_VALUES",
    "DEFAULT_EMOTION",
    "DEFAULT_SPEECH_STYLE",
    "DEFAULT_SPEAKING_SPEED",
    "DEFAULT_UTTERANCE_GOAL",
    "VALID_EMOTIONS",
    "VALID_SPEECH_STYLES",
    "VALID_SPEAKING_SPEEDS",
    "VALID_UTTERANCE_GOALS",
]
