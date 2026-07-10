"""Split long sentences before MT; merge after translation (timing unchanged)."""

from __future__ import annotations

import re
from typing import Any

MAX_WORDS = 18
MIN_WORDS = 4

_PUNCT_SPLIT_RE = re.compile(r"(?<=[,;:—–-])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_CONJ_RE = re.compile(
    r"\b(and|but|because|so|when|while|if|after|before|or|although|though|since|until|unless)\b",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", str(text or "")))


def split_for_translation(text: str) -> list[str]:
    """
    Break text into MT-friendly chunks (10–18 words max).
    Splits on punctuation and conjunctions; preserves order.
    """
    t = " ".join(str(text or "").split()).strip()
    if not t:
        return []
    if _word_count(t) <= MAX_WORDS:
        return [t]

    parts: list[str] = []

    def _split_chunk(chunk: str) -> None:
        chunk = chunk.strip()
        if not chunk:
            return
        if _word_count(chunk) <= MAX_WORDS:
            parts.append(chunk)
            return

        # Try punctuation splits first
        subs = [s.strip() for s in _PUNCT_SPLIT_RE.split(chunk) if s.strip()]
        if len(subs) > 1:
            for sub in subs:
                _split_chunk(sub)
            return

        # Try conjunction splits
        conj_parts: list[str] = []
        last = 0
        for m in _CONJ_RE.finditer(chunk):
            pos = m.start()
            if pos > last:
                left = chunk[last:pos].strip()
                if left and _word_count(left) >= MIN_WORDS:
                    conj_parts.append(left)
                    last = pos
        tail = chunk[last:].strip()
        if tail:
            conj_parts.append(tail)

        if len(conj_parts) > 1:
            for cp in conj_parts:
                _split_chunk(cp)
            return

        # Hard split by word count
        words = chunk.split()
        for i in range(0, len(words), MAX_WORDS):
            parts.append(" ".join(words[i : i + MAX_WORDS]))

    for sent in _SENTENCE_END.split(t):
        _split_chunk(sent)

    return [p for p in parts if p.strip()]


def merge_translated_parts(parts: list[str]) -> str:
    """Rejoin translated chunks into one segment."""
    cleaned = [str(p or "").strip() for p in parts if str(p or "").strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return " ".join(cleaned)


def split_meta(original: str, parts: list[str]) -> dict[str, Any]:
    return {
        "split": len(parts) > 1,
        "part_count": len(parts),
        "original_words": _word_count(original),
    }
