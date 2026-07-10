"""Mixed language detection — RU in UK and vice versa."""

from __future__ import annotations

import re

from engines.translation_quality_score import _UK_RUISM_WORDS

_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)

# Ukrainian-specific markers (should not appear in Russian output)
_UK_MARKERS = frozenset(
    {
        "що", "який", "яка", "яке", "які", "він", "вона", "вони", "їх", "її",
        "немає", "зараз", "дуже", "теж", "також", "якщо", "коли", "тільки",
        "молодший", "молодша",
    }
)


def mixed_language_percent(text: str, *, tgt_lang: str) -> float:
    """
    Percent of words that belong to the wrong language script/vocabulary.
    For uk target: Russian words in Ukrainian text.
    For ru target: Ukrainian-only words in Russian text.
    """
    lang = (tgt_lang or "uk").split("-")[0].lower()
    words = [w.lower() for w in _WORD.findall(str(text or ""))]
    if not words:
        return 0.0

    foreign = 0
    if lang == "uk":
        for w in words:
            if w in _UK_RUISM_WORDS:
                foreign += 1
    elif lang == "ru":
        for w in words:
            if w in _UK_MARKERS:
                foreign += 1

    return round(foreign / len(words) * 100.0, 2)


def exceeds_mixed_threshold(text: str, *, tgt_lang: str, threshold: float = 3.0) -> bool:
    return mixed_language_percent(text, tgt_lang=tgt_lang) > threshold
