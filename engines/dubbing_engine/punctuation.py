"""
Stage 3 — Punctuation restoration.

Ensures every TTS segment has proper punctuation.
Punctuation marks drive Edge TTS pauses (periods → longer, commas → shorter).
"""

from __future__ import annotations

import re

# Map final punctuation to the approximate TTS pause it creates (for logging)
PUNCT_PAUSE_GUIDE: dict[str, int] = {
    ".":  160,
    "?":  150,
    "!":  150,
    "…":  200,
    ";":  100,
    ":":  90,
    ",":  80,
}

# Sentence-ending punctuation characters
_SENTENCE_ENDERS = frozenset(".?!…")

# Allowed terminal punctuation (do not add period if text ends with these)
_TERMINAL_OK = frozenset(".?!…")

# Common abbreviations that should NOT be followed by a sentence-ending period
_ABBREV_RE = re.compile(
    r"\b(?:Jr|Sr|Dr|Mr|Mrs|Ms|Prof|Dept|Govt|Corp|Inc|Ltd|etc|vs|approx|est)\.$",
    re.IGNORECASE,
)

# Multiple spaces / repeated punctuation fixes
_MULTI_SPACE = re.compile(r"[ \t]+")
_REPEATED_PUNCT = re.compile(r"([.!?])\1{2,}")  # "!!!" → "!"
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?;:…])")


def restore_punctuation(text: str) -> tuple[str, list[str]]:
    """
    Ensure the segment has correct punctuation for natural TTS delivery.

    Returns (corrected_text, list_of_changes_applied).
    """
    if not text:
        return text, []

    changes: list[str] = []
    original = text

    # 1. Fix space-before-punctuation: "hello , world" → "hello, world"
    fixed = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    if fixed != text:
        changes.append("removed_space_before_punct")
        text = fixed

    # 2. Collapse repeated identical punctuation: "!!!" → "!"
    fixed = _REPEATED_PUNCT.sub(r"\1", text)
    if fixed != text:
        changes.append("collapsed_repeated_punct")
        text = fixed

    # 3. Collapse multiple spaces
    fixed = _MULTI_SPACE.sub(" ", text).strip()
    if fixed != text:
        changes.append("collapsed_spaces")
        text = fixed

    # 4. Ensure sentence ends with punctuation
    stripped = text.rstrip()
    last_char = stripped[-1] if stripped else ""
    if last_char not in _TERMINAL_OK:
        # Don't add period after an abbreviation that already has one
        if not _ABBREV_RE.search(stripped):
            text = stripped + "."
            changes.append("added_terminal_period")

    # 5. Capitalise first word if it's all-lowercase after a period
    if text and not text[0].isupper() and text[0].isalpha():
        text = text[0].upper() + text[1:]
        changes.append("capitalised_first_word")

    return text, changes


def terminal_pause_ms(text: str) -> int:
    """Return the natural post-sentence pause for this text, in ms."""
    stripped = (text or "").rstrip()
    if not stripped:
        return 120
    ch = stripped[-1]
    return PUNCT_PAUSE_GUIDE.get(ch, 120)


def split_into_sentences(text: str) -> list[str]:
    """
    Split text at sentence boundaries (for multi-sentence segments).
    Preserves punctuation at the end of each sentence.
    """
    if not text:
        return []
    # Split on ". ", "! ", "? ", "… " followed by an uppercase letter
    parts = re.split(r'(?<=[.!?…])\s+(?=[А-ЯЁІЇЄA-Z])', text.strip())
    return [p.strip() for p in parts if p.strip()]


def count_sentences(text: str) -> int:
    return len(split_into_sentences(text))


def has_punctuation(text: str) -> bool:
    """True if text contains any sentence-ending punctuation."""
    return bool(re.search(r'[.!?…;,]', text or ""))
