"""Repetition guard — remove duplicated sentences / clause runs before TTS.

TZ §8: detect and remove repeated sentences, paragraphs, and meaning blocks
introduced by MT/naturalizer BEFORE the text reaches TTS. Conservative by
design: it only drops verbatim or near-verbatim consecutive repeats, never
rephrases or removes unique content (so meaning is preserved, TZ §4/§6).
"""

from __future__ import annotations

import re
import unicodedata

# Sentence boundary: keep the terminal punctuation with the sentence.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
# Minimum repeated word-run length to treat as an accidental duplicate.
_MIN_RUN_WORDS = 3


def _normalize(text: str) -> str:
    """Lowercase, drop combining stress marks, strip punctuation, collapse spaces."""
    s = unicodedata.normalize("NFD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s, flags=re.UNICODE)
    return " ".join(s.split())


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(str(text or "").strip())
    return [p for p in (s.strip() for s in parts) if p]


def has_repetition(text: str) -> bool:
    """True if the text contains a repeated sentence or repeated word-run."""
    _, changed = remove_repeated_sentences(text)
    return changed


def _dedupe_sentences(sentences: list[str]) -> tuple[list[str], bool]:
    kept: list[str] = []
    kept_norm: list[str] = []
    changed = False
    for sent in sentences:
        norm = _normalize(sent)
        if not norm:
            kept.append(sent)
            continue
        drop = False
        for j, prev in enumerate(kept_norm):
            if not prev:
                continue
            if norm == prev:
                drop = True
                break
            # Current is a truncated repeat of an already-kept sentence.
            if len(norm) >= 8 and (prev.startswith(norm) or norm.startswith(prev)):
                # Keep the longer (more complete) of the two.
                if len(norm) > len(prev):
                    kept[j] = sent
                    kept_norm[j] = norm
                drop = True
                break
        if drop:
            changed = True
            continue
        kept.append(sent)
        kept_norm.append(norm)
    return kept, changed


def _collapse_repeated_word_runs(text: str) -> tuple[str, bool]:
    """Collapse an immediately-repeated run of >= _MIN_RUN_WORDS words.

    Example: "він робив поворот він робив поворот" → "він робив поворот".
    Comparison ignores stress marks / case so TTS stress diacritics don't hide
    a duplicate.
    """
    words = str(text or "").split()
    n = len(words)
    if n < _MIN_RUN_WORDS * 2:
        return text, False
    norm_words = [_normalize(w) for w in words]
    changed = False
    i = 0
    out: list[str] = []
    while i < n:
        collapsed = False
        # Try the longest run first, down to the minimum length.
        max_run = (n - i) // 2
        for run in range(max_run, _MIN_RUN_WORDS - 1, -1):
            a = norm_words[i : i + run]
            b = norm_words[i + run : i + 2 * run]
            if a == b and all(a):
                out.extend(words[i : i + run])
                i += 2 * run
                changed = True
                collapsed = True
                break
        if not collapsed:
            out.append(words[i])
            i += 1
    return " ".join(out), changed


def remove_repeated_sentences(text: str) -> tuple[str, bool]:
    """Remove duplicated sentences and immediately-repeated word runs.

    Returns (cleaned_text, changed). Pure cleanup — never rephrases.
    """
    raw = str(text or "").strip()
    if not raw:
        return raw, False

    sentences = _split_sentences(raw)
    cleaned_sents, sent_changed = _dedupe_sentences(sentences)
    joined = " ".join(cleaned_sents) if cleaned_sents else raw

    collapsed, run_changed = _collapse_repeated_word_runs(joined)
    collapsed = " ".join(collapsed.split())
    return collapsed, bool(sent_changed or run_changed)


def dedupe_segment_texts(texts: list[str]) -> tuple[list[str], list[int]]:
    """Apply repetition removal to each segment; return (cleaned, changed_indices)."""
    out: list[str] = []
    changed_idx: list[int] = []
    for i, t in enumerate(texts):
        cleaned, changed = remove_repeated_sentences(t)
        out.append(cleaned)
        if changed:
            changed_idx.append(i)
    return out, changed_idx


def dedupe_adjacent_copies(texts: list[str]) -> list[str]:
    """Blank adjacent segments that repeat the previous spoken line (Stage 37)."""
    out: list[str] = []
    prev_norm = ""
    for t in texts:
        cur = str(t or "").strip()
        try:
            from engines.text_slot_fit import strip_slot_pad_fillers

            core = _normalize(strip_slot_pad_fillers(cur))
        except Exception:
            core = _normalize(cur)
        if core and prev_norm and (core == prev_norm or core in prev_norm or prev_norm in core):
            # Same utterance (or pad-stripped clone) as the previous line.
            if len(core) >= 8 or core == prev_norm:
                out.append("")
                continue
        out.append(cur)
        if core:
            prev_norm = core
    return out
