"""TTS-friendly pronunciation — clusters, triple consonants, word boundaries."""

from __future__ import annotations

import re

_TRIPLE_CONSONANT_RE = re.compile(
    r"([бвгджзклмнпрстфхцчшщbcdfghjklmnpqrstvwxyz])\1{2,}",
    re.IGNORECASE,
)

_AWKWARD_CLUSTERS_RU: list[tuple[str, str]] = [
    (r"\bвздр", "вз др"),
    (r"\bвзг", "вз г"),
    (r"\bстрн", "стр н"),
    (r"\bздрв", "здр в"),
    (r"\bвстр", "в стр"),
]

_HYPHEN_GLUE_RE = re.compile(r"(\w)-(\w)")


def fix_triple_consonants(text: str) -> str:
    """Insert soft break between triple consonant runs for TTS."""
    out = str(text or "")

    def _break_triple(match: re.Match) -> str:
        chars = match.group(0)
        if len(chars) < 3:
            return chars
        return chars[:2] + "\u200b" + chars[2:]

    return _TRIPLE_CONSONANT_RE.sub(_break_triple, out)


def fix_awkward_clusters(text: str, tgt_lang: str = "ru") -> str:
    out = str(text or "")
    if tgt_lang == "ru":
        for pat, repl in _AWKWARD_CLUSTERS_RU:
            out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out


def fix_word_boundaries(text: str) -> str:
    """Normalize spacing around punctuation for cleaner TTS pauses."""
    out = str(text or "").strip()
    out = re.sub(r"\s+([,.!?;:—])", r"\1", out)
    out = re.sub(r"([,.!?;:—])(?=[^\s])", r"\1 ", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def optimize_pronunciation(text: str, *, tgt_lang: str = "ru") -> str:
    """Pass 5 — TTS-friendly polish."""
    out = str(text or "").strip()
    if not out:
        return out
    out = fix_triple_consonants(out)
    out = fix_awkward_clusters(out, tgt_lang)
    out = fix_word_boundaries(out)
    return out.strip() or text
