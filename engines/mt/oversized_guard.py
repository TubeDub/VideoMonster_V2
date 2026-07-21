"""HF1 — Oversized MT unit guard: split before MT (max chars / sentences)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from engines.mt.sentence_split import split_mt_sentences

logger = logging.getLogger("tubedub.mt.oversized_guard")


def _max_chars() -> int:
    try:
        return max(200, int(os.getenv("MT_MAX_CHARS_PER_UNIT", "480")))
    except ValueError:
        return 480


def _max_sentences() -> int:
    try:
        return max(1, int(os.getenv("MT_MAX_SENTENCES_PER_UNIT", "2")))
    except ValueError:
        return 2


def _max_words() -> int:
    try:
        return max(20, int(os.getenv("MT_MAX_WORDS_PER_UNIT", "55")))
    except ValueError:
        return 55


@dataclass
class OversizedSplitResult:
    texts: list[str]
    parent_indices: list[int]  # original segment index for each output unit
    split_count: int = 0
    oversized_logged: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_count": len(self.texts),
            "split_count": self.split_count,
            "oversized": list(self.oversized_logged),
        }


def is_oversized_mt_unit(text: str) -> bool:
    clean = " ".join(str(text or "").split())
    if not clean:
        return False
    sents = split_mt_sentences(clean)
    words = len(clean.split())
    return (
        len(clean) > _max_chars()
        or len(sents) > _max_sentences()
        or words > _max_words()
    )


def split_oversized_unit(text: str) -> list[str]:
    """Split one MT unit on sentence boundaries; further chunk if still huge."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return []
    if not is_oversized_mt_unit(clean):
        return [clean]

    parts = split_mt_sentences(clean)
    if len(parts) <= 1:
        # Soft word-window split as last resort
        words = clean.split()
        max_w = _max_words()
        chunks: list[str] = []
        for i in range(0, len(words), max_w):
            chunk = " ".join(words[i : i + max_w]).strip()
            if chunk:
                chunks.append(chunk)
        return chunks or [clean]

    # Pack sentences into units respecting max_sentences / max_chars
    max_s = _max_sentences()
    max_c = _max_chars()
    packed: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for sent in parts:
        s = sent.strip()
        if not s:
            continue
        next_len = buf_len + len(s) + (1 if buf else 0)
        if buf and (len(buf) >= max_s or next_len > max_c):
            packed.append(" ".join(buf))
            buf = [s]
            buf_len = len(s)
        else:
            buf.append(s)
            buf_len = next_len
    if buf:
        packed.append(" ".join(buf))
    return packed or [clean]


def guard_segments_before_mt(
    segments: list[str],
    *,
    log: bool = True,
) -> OversizedSplitResult:
    """Expand oversized segments into MT-safe units; track parent indices.

    Caller that needs 1:1 segment count should use ``split_in_place`` instead.
    """
    texts: list[str] = []
    parents: list[int] = []
    oversized: list[dict[str, Any]] = []
    split_count = 0
    for i, raw in enumerate(segments):
        clean = " ".join(str(raw or "").split())
        if not clean:
            texts.append("")
            parents.append(i)
            continue
        if is_oversized_mt_unit(clean):
            parts = split_oversized_unit(clean)
            if log:
                entry = {
                    "index": i,
                    "chars": len(clean),
                    "words": len(clean.split()),
                    "sentences": len(split_mt_sentences(clean)),
                    "parts": len(parts),
                }
                oversized.append(entry)
                logger.warning(
                    "[MT Guard] oversized seg#%d chars=%d words=%d sents=%d → %d parts",
                    i + 1,
                    entry["chars"],
                    entry["words"],
                    entry["sentences"],
                    entry["parts"],
                )
            if len(parts) > 1:
                split_count += 1
            for p in parts:
                texts.append(p)
                parents.append(i)
        else:
            texts.append(clean)
            parents.append(i)
    return OversizedSplitResult(
        texts=texts, parent_indices=parents, split_count=split_count, oversized_logged=oversized
    )


def split_in_place(segments: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    """Keep 1:1 count: if oversized, keep first packed unit in slot and
    append remainder notes for logging. Prefer translating full text by
    sentence-joining after per-sentence MT — use ``translate_oversized_safely``.
    """
    out = list(segments)
    meta: list[dict[str, Any]] = []
    for i, raw in enumerate(list(out)):
        if is_oversized_mt_unit(raw):
            parts = split_oversized_unit(raw)
            meta.append({"index": i, "parts": parts, "was_oversized": True})
            # Store joined marker; actual MT should use parts
            out[i] = raw  # unchanged surface for audits; parts in meta
    return out, meta


def translate_oversized_safely(
    text: str,
    translate_fn,
) -> str:
    """Translate one segment; if oversized, split → translate parts → join."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    if not is_oversized_mt_unit(clean):
        return str(translate_fn(clean) or "").strip()
    parts = split_oversized_unit(clean)
    logger.warning(
        "[MT Guard] translating oversized (%d chars) as %d units",
        len(clean),
        len(parts),
    )
    translated = []
    for p in parts:
        translated.append(str(translate_fn(p) or "").strip())
    return " ".join(t for t in translated if t).strip()
