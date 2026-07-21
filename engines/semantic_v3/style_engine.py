"""P113 — Style Engine (pre-Translation / Dub)."""

from __future__ import annotations

import os
import re
from typing import Iterable

from engines.semantic_v3.types import SemanticSentence

SUPPORTED_STYLES: tuple[str, ...] = (
    "Movie",
    "Series",
    "Documentary",
    "Interview",
    "Podcast",
    "YouTube",
    "Anime",
    "Cartoon",
    "Kids",
    "News",
    "Lecture",
    "Gaming",
)

_STYLE_HINTS: dict[str, re.Pattern[str]] = {
    "News": re.compile(r"(?i)\b(breaking|reported|according to|новин|повідомляє)\b"),
    "Kids": re.compile(r"(?i)\b(mommy|daddy|toy|kids|мамо|тату|іграшк)\b"),
    "Gaming": re.compile(r"(?i)\b(level|boss|respawn|hp|quest|рівень|бос)\b"),
    "Lecture": re.compile(r"(?i)\b(therefore|hypothesis|chapter|отже|гіпотез|розділ)\b"),
    "Documentary": re.compile(r"(?i)\b(in nature|scientists|historically|природ|вчен)\b"),
    "Podcast": re.compile(r"(?i)\b(welcome back|subscribe|episode|епізод|підкаст)\b"),
    "YouTube": re.compile(r"(?i)\b(like and subscribe|comment below|сьогодні ми)\b"),
    "Anime": re.compile(r"(?i)\b(senpai|chan|kun|sensei)\b"),
    "Interview": re.compile(r"(?i)\b(tell us|how do you feel|розкажіть|як ви)\b"),
}


def detect_style(
    sentences: Iterable[SemanticSentence],
    *,
    content_mode: str = "",
    hint: str = "",
) -> str:
    """Return one of SUPPORTED_STYLES."""
    forced = (hint or os.environ.get("VM_SEMANTIC_STYLE", "") or "").strip()
    if forced:
        for s in SUPPORTED_STYLES:
            if s.lower() == forced.lower():
                return s
    mode = (content_mode or "").strip().lower()
    mode_map = {
        "movie": "Movie",
        "film": "Movie",
        "series": "Series",
        "documentary": "Documentary",
        "interview": "Interview",
        "podcast": "Podcast",
        "youtube": "YouTube",
        "anime": "Anime",
        "cartoon": "Cartoon",
        "kids": "Kids",
        "news": "News",
        "lecture": "Lecture",
        "gaming": "Gaming",
        "game": "Gaming",
    }
    if mode in mode_map:
        return mode_map[mode]

    joined = " ".join(s.text for s in sentences)
    scores = {name: 0 for name in SUPPORTED_STYLES}
    for name, pat in _STYLE_HINTS.items():
        scores[name] = len(pat.findall(joined))
    # Dialogue density → Series/Interview
    dlg = sum(1 for s in sentences if s.is_dialogue or s.is_question)
    if dlg >= max(2, len(list(sentences)) // 3):
        scores["Interview"] += 2
        scores["Series"] += 1
    best = max(SUPPORTED_STYLES, key=lambda n: scores.get(n, 0))
    if scores.get(best, 0) <= 0:
        return "Movie"
    return best


def apply_style_engine(
    sentences: list[SemanticSentence],
    *,
    content_mode: str = "",
    hint: str = "",
) -> str:
    style = detect_style(sentences, content_mode=content_mode, hint=hint)
    for s in sentences:
        s.style = style
        s.context = {**(s.context or {}), "style": style}
    return style
