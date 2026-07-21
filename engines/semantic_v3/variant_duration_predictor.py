"""P107 — Duration Prediction for adaptation variants (before TTS).

Each variant gets:
- expected_duration_ms: predicted TTS output duration
- prediction_confidence: 0.0-1.0 confidence in the prediction

Uses syllable/phoneme-based estimation (no actual TTS call).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.semantic_v3.variant_duration_predictor")

# Average ms per syllable by language (empirical from Edge-TTS)
_SYLLABLE_RATE: dict[str, float] = {
    "en": 180.0,
    "uk": 160.0,
    "ua": 160.0,
    "ru": 155.0,
    "de": 175.0,
    "fr": 170.0,
    "es": 165.0,
    "it": 165.0,
    "pt": 170.0,
    "ja": 200.0,
    "ko": 190.0,
    "zh": 250.0,
}

_PAUSE_AFTER_PUNCT: dict[str, int] = {
    ".": 250,
    "!": 280,
    "?": 300,
    ",": 120,
    ";": 180,
    ":": 150,
    "—": 200,
    "–": 180,
    "...": 350,
    "…": 350,
}

_VOWELS_CYRILLIC = set("аеєиіїоуюяё")
_VOWELS_LATIN = set("aeiouy")


def count_syllables(text: str) -> int:
    """Count syllables using vowel groups heuristic."""
    text = text.lower().strip()
    if not text:
        return 0

    count = 0
    in_vowel = False
    for ch in text:
        is_vowel = ch in _VOWELS_LATIN or ch in _VOWELS_CYRILLIC
        if is_vowel and not in_vowel:
            count += 1
        in_vowel = is_vowel

    return max(1, count)


def estimate_pause_ms(text: str) -> int:
    """Estimate total internal pause time from punctuation."""
    total = 0
    for punct, ms in _PAUSE_AFTER_PUNCT.items():
        total += text.count(punct) * ms
    return total


def predict_variant_duration(
    text: str,
    *,
    lang: str = "uk",
    speech_rate: float = 1.0,
) -> tuple[int, float]:
    """Predict TTS duration for a text variant.

    Returns (expected_duration_ms, prediction_confidence).
    """
    if not text or not text.strip():
        return 0, 1.0

    syllables = count_syllables(text)
    rate = _SYLLABLE_RATE.get(lang, 175.0)

    base_ms = int(syllables * rate / max(0.5, speech_rate))
    pause_ms = estimate_pause_ms(text)
    total_ms = base_ms + pause_ms

    word_count = len(text.split())
    confidence = 0.85
    if word_count < 3:
        confidence = 0.70
    elif word_count > 30:
        confidence = 0.75

    if re.search(r'\d+', text):
        confidence -= 0.05

    if re.search(r'\b[A-Z]{2,}\b', text):
        confidence -= 0.05

    return total_ms, round(min(1.0, max(0.1, confidence)), 3)


def predict_all_variants(
    variants: list[Any],
    *,
    lang: str = "uk",
    speech_rate: float = 1.0,
) -> list[Any]:
    """Apply duration prediction to all AdaptationVariant objects."""
    for var in variants:
        text = getattr(var, 'text', '') or ''
        dur, conf = predict_variant_duration(text, lang=lang, speech_rate=speech_rate)
        var.predicted_duration_ms = dur
        var.prediction_confidence = conf
    return variants


def compute_duration_score(
    predicted_ms: int,
    slot_ms: int,
    *,
    tolerance_pct: float = 15.0,
) -> float:
    """Score 0-100 how well predicted duration fits the slot.

    100 = perfect fit
    0 = severe overflow (>50% over) or severe underflow (>50% under)
    """
    if slot_ms <= 0:
        return 50.0

    ratio = predicted_ms / slot_ms

    if ratio <= 1.0:
        if ratio >= 0.7:
            return 100.0 - (1.0 - ratio) * 100
        return max(0, 100.0 - (1.0 - ratio) * 200)
    else:
        excess = ratio - 1.0
        tolerance = tolerance_pct / 100.0
        if excess <= tolerance:
            return 100.0 - (excess / tolerance) * 30
        return max(0, 70.0 - (excess - tolerance) * 200)
