"""
Accurate TTS duration predictor.

Uses syllable counting + punctuation pauses instead of raw character count.
Syllable count correlates much better with TTS output duration than char count,
especially for Slavic languages where words can be very long but fast.

Thresholds (per ТЗ):
  ratio < 1.00          → fits with room
  1.00 ≤ ratio < 1.15   → fits (within tolerance) — PASS, NO changes allowed
  1.15 ≤ ratio < 1.25   → Rephrase / Synonyms / Word Order allowed (NO deletion)
  ratio ≥ 1.25          → all steps allowed including secondary reduction
  ratio ≥ 1.50          → overflow — after all steps, recommend merge_next
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── Syllable/phoneme rates (syllables per second) for Edge TTS ─────────────────
# Calibrated against real Edge TTS output at default speed.
_SYLLABLES_PER_SEC: dict[str, float] = {
    "uk": 4.8,    # Ukrainian: longer words, medium speed
    "ru": 5.0,    # Russian: similar to Ukrainian
    "de": 4.5,    # German: very long compound words
    "en": 5.8,    # English: shorter words, faster delivery
    "fr": 5.5,
    "es": 5.6,
    "pl": 4.7,
    "cs": 4.7,
    "sk": 4.7,
}
_DEFAULT_SPS: float = 4.9

# Vowel sets by language (used for syllable counting)
_VOWELS: dict[str, str] = {
    "uk": "аеиіоуєїюяАЕИІОУЄЇЮЯ",
    "ru": "аеёиоуыэюяАЕЁИОУЫЭЮЯ",
    "de": "aeiouäöüAEIOUÄÖÜ",
    "en": "aeiouAEIOU",
    "fr": "aàâeéèêëiîïoôuùûüyAÀÂEÉÈÊËIÎÏOÔUÙÛÜY",
    "es": "aeiouáéíóúüAEIOUÁÉÍÓÚÜ",
}
_DEFAULT_VOWELS = "aeiouаеиоу"

# Punctuation pauses (ms) added to the speech duration
_PUNCT_PAUSES: dict[str, int] = {
    ".":  160,
    "?":  150,
    "!":  150,
    "…":  220,
    ";":  110,
    ":":  90,
    ",":  80,
    "—":  70,
    "-":  30,
}

# Minimum duration per spoken word (safety floor)
_MIN_MS_PER_WORD: int = 130

# ── Thresholds ─────────────────────────────────────────────────────────────────
PASS_THRESHOLD:        float = 1.15  # < this → PASS, no adaptation allowed
REPHRASE_THRESHOLD:   float = 1.25  # < this → only safe restructuring (no deletion)
REDUCTION_THRESHOLD:  float = 1.25  # ≥ this → secondary reduction allowed
OVERFLOW_THRESHOLD:   float = 1.50  # ≥ this → overflow warning, recommend merge


@dataclass
class PredictorResult:
    """Full prediction result with decision and audit trail."""
    text: str
    lang: str
    slot_ms: int
    predicted_ms: int
    ratio: float                  # predicted / slot
    overflow_ms: int              # max(0, predicted - slot)
    decision: str                 # PASS | REPHRASE | REDUCE | OVERFLOW | NO_SLOT
    syllables: int
    words: int
    punct_pause_ms: int
    iteration: int = 0            # which adaptation round produced this
    note: str = ""

    def log_line(self) -> str:
        """Single-line summary for the audit log."""
        sign = "+" if self.overflow_ms > 0 else ""
        return (
            f"Seg | Target: {self.slot_ms}ms | "
            f"Prediction: {self.predicted_ms}ms | "
            f"Diff: {sign}{self.overflow_ms}ms | "
            f"Ratio: {self.ratio:.2f} | "
            f"Decision: {self.decision}"
        )

    @property
    def passes(self) -> bool:
        return self.decision in ("PASS", "NO_SLOT")

    @property
    def may_reduce(self) -> bool:
        return self.ratio >= REDUCTION_THRESHOLD

    @property
    def may_rephrase_only(self) -> bool:
        return PASS_THRESHOLD <= self.ratio < REDUCTION_THRESHOLD


def count_syllables(text: str, lang: str) -> int:
    """Count vowel nuclei (syllable approximation)."""
    base = (lang or "en").split("-")[0].lower()
    vowel_set = set(_VOWELS.get(base, _DEFAULT_VOWELS))
    count = sum(1 for ch in (text or "") if ch in vowel_set)
    # Minimum: one syllable per word
    words = len((text or "").split())
    return max(count, words)


def predict(text: str, lang: str, slot_ms: int = 0, iteration: int = 0) -> PredictorResult:
    """
    Predict TTS duration using syllable model.

    Args:
        text:      Text to synthesise.
        lang:      Target language code ('uk', 'ru', 'de', 'en', …).
        slot_ms:   Available slot in milliseconds (0 = unknown).
        iteration: Which adaptation iteration produced this text.

    Returns:
        PredictorResult with predicted_ms, ratio, decision, and audit data.
    """
    t = (text or "").strip()
    if not t:
        return PredictorResult(
            text=t, lang=lang, slot_ms=slot_ms, predicted_ms=0, ratio=0.0,
            overflow_ms=0, decision="PASS", syllables=0, words=0, punct_pause_ms=0,
        )

    base = (lang or "en").split("-")[0].lower()
    sps = _SYLLABLES_PER_SEC.get(base, _DEFAULT_SPS)

    # Syllable-based base time
    syllables = count_syllables(t, base)
    base_ms = int((syllables / sps) * 1000)

    # Punctuation pauses
    punct_ms = sum(
        t.count(ch) * pause
        for ch, pause in _PUNCT_PAUSES.items()
    )

    # Word-floor: never under-estimate very short texts
    words = len(t.split())
    floor_ms = words * _MIN_MS_PER_WORD

    predicted_ms = max(base_ms + punct_ms, floor_ms)

    # Decision
    if slot_ms <= 0:
        decision = "NO_SLOT"
        ratio = 0.0
        overflow_ms = 0
    else:
        ratio = predicted_ms / slot_ms
        overflow_ms = max(0, predicted_ms - slot_ms)
        if ratio < PASS_THRESHOLD:
            decision = "PASS"
        elif ratio < REDUCTION_THRESHOLD:
            decision = "REPHRASE"
        elif ratio < OVERFLOW_THRESHOLD:
            decision = "REDUCE"
        else:
            decision = "OVERFLOW"

    return PredictorResult(
        text=t,
        lang=lang,
        slot_ms=slot_ms,
        predicted_ms=predicted_ms,
        ratio=ratio,
        overflow_ms=overflow_ms,
        decision=decision,
        syllables=syllables,
        words=words,
        punct_pause_ms=punct_ms,
        iteration=iteration,
    )


def predict_ms(text: str, lang: str) -> int:
    """Convenience: just the predicted milliseconds."""
    return predict(text, lang, slot_ms=0).predicted_ms


def fits(text: str, lang: str, slot_ms: int) -> bool:
    """True if text fits in slot (ratio < PASS_THRESHOLD)."""
    if slot_ms <= 0:
        return True
    return predict(text, lang, slot_ms).decision == "PASS"
