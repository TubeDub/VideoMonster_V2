"""Contextual optional filler words — Smart Segment Optimizer."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Необязательные вводные (TZ §4). Удаление только контекстное.
_OPTIONAL_FILLERS_UK: frozenset[str] = frozenset(
    {
        "але",
        "отже",
        "тож",
        "проте",
        "власне",
        "фактично",
        "справді",
        "якраз",
        "взагалі",
        "ну",
        "тобто",
        "от",
        "значить",
        "типу",
        "короче",
    }
)

_OPTIONAL_FILLERS_RU: frozenset[str] = frozenset(
    {
        "ну",
        "в общем",
        "собственно",
        "кстати",
        "как бы",
        "типа",
        "то есть",
        "в принципе",
        "на самом деле",
        "значит",
        "короче",
        "просто",
    }
)

_OPTIONAL_FILLERS_EN: frozenset[str] = frozenset(
    {
        "well",
        "so",
        "like",
        "you know",
        "basically",
        "actually",
        "literally",
        "just",
        "okay",
        "ok",
        "i mean",
    }
)

_CONTRAST_MARKERS = frozenset({"але", "but", "however", "проте", "однако", "та"})


@dataclass
class FillerStep:
    text: str
    removed: str
    reason: str


def _filler_set(lang: str) -> frozenset[str]:
    base = (lang or "en").split("-")[0].lower()
    if base == "uk":
        return _OPTIONAL_FILLERS_UK | _OPTIONAL_FILLERS_RU
    if base == "ru":
        return _OPTIONAL_FILLERS_RU | _OPTIONAL_FILLERS_UK
    return _OPTIONAL_FILLERS_EN


def _tokenize_with_spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def _is_safe_to_remove(
    word: str,
    *,
    position: int,
    total_words: int,
    lang: str,
) -> tuple[bool, str]:
    low = word.lower().strip(".,!?…;:")
    fillers = _filler_set(lang)

    if low not in fillers:
        return False, "not_a_filler"

    if total_words <= 4:
        return False, "sentence_too_short"

    if low in _CONTRAST_MARKERS and 0 < position < total_words - 1:
        return False, "contrast_connector"

    # Sentence-initial "Але/But" with following contrast — keep
    if position == 0 and low in _CONTRAST_MARKERS and total_words > 5:
        return False, "leading_contrast"

    return True, "optional_filler"


def text_has_phrase_at(word: str, phrase: str) -> bool:
    return word.lower().startswith(phrase)


def iter_filler_removals(text: str, lang: str) -> list[FillerStep]:
    """Ordered list of texts with one more contextual filler removed each step."""
    out: list[FillerStep] = []
    current = " ".join(str(text or "").split())
    if not current:
        return out

    seen: set[str] = set()
    for _ in range(12):
        tokens = _tokenize_with_spans(current)
        if len(tokens) <= 3:
            break
        removed_any = False
        for i, (word, start, end) in enumerate(tokens):
            low = word.lower().strip(".,!?…;:")
            ok, reason = _is_safe_to_remove(
                word, position=i, total_words=len(tokens), lang=lang
            )
            if not ok:
                continue
            fillers = _filler_set(lang)
            if low not in fillers:
                continue
            before = current[:start].rstrip()
            after = current[end:].lstrip()
            candidate = " ".join(p for p in (before, after) if p).strip()
            candidate = re.sub(r"\s+([,.!?])", r"\1", candidate)
            if not candidate or candidate in seen or candidate == current:
                continue
            seen.add(candidate)
            out.append(
                FillerStep(
                    text=candidate,
                    removed=word,
                    reason=f"removed optional filler '{word}' ({reason})",
                )
            )
            current = candidate
            removed_any = True
            break
        if not removed_any:
            break
    return out
