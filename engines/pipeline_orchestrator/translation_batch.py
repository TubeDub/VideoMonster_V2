"""Batch grouping for Marian / LLM — 300–800 tokens or 3–6 sentences per batch."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

# Rough token estimate (Latin/Cyrillic mixed dubbing text).
_CHARS_PER_TOKEN = 4
_MIN_TOKENS = 300
_MAX_TOKENS = 800
_MIN_SENTENCES = 3
_MAX_SENTENCES = 6

_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def estimate_tokens(text: str) -> int:
    t = str(text or "").strip()
    if not t:
        return 0
    return max(1, len(t) // _CHARS_PER_TOKEN)


def count_sentences(text: str) -> int:
    t = str(text or "").strip()
    if not t:
        return 0
    parts = [p for p in _SENT_SPLIT.split(t) if p.strip()]
    return max(1, len(parts))


@dataclass
class TranslationBatch:
    """One batch flowing through Marian or LLM stage."""

    batch_id: int
    segment_indices: list[int] = field(default_factory=list)
    source_texts: list[str] = field(default_factory=list)
    timing_spans: list[dict[str, Any]] = field(default_factory=list)
    combined_source: str = ""
    delimiter: str = " \n\n "

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.combined_source)


def build_translation_batches(
    segments: Sequence[str],
    timing_map: Sequence[Any] | None = None,
    *,
    min_tokens: int = _MIN_TOKENS,
    max_tokens: int = _MAX_TOKENS,
    min_sentences: int = _MIN_SENTENCES,
    max_sentences: int = _MAX_SENTENCES,
) -> list[TranslationBatch]:
    """Group consecutive segments into batches for Marian/LLM inference."""
    batches: list[TranslationBatch] = []
    cur_indices: list[int] = []
    cur_texts: list[str] = []
    cur_sents = 0
    cur_tokens = 0
    batch_id = 0

    def _flush() -> None:
        nonlocal batch_id, cur_indices, cur_texts, cur_sents, cur_tokens
        if not cur_indices:
            return
        combined = " \n\n ".join(t for t in cur_texts if t.strip()).strip()
        spans: list[dict[str, Any]] = []
        if timing_map is not None:
            for idx in cur_indices:
                if idx < len(timing_map):
                    tm = timing_map[idx]
                    if isinstance(tm, dict):
                        spans.append(dict(tm))
                    else:
                        spans.append({"index": idx})
        batches.append(
            TranslationBatch(
                batch_id=batch_id,
                segment_indices=list(cur_indices),
                source_texts=list(cur_texts),
                timing_spans=spans,
                combined_source=combined,
            )
        )
        batch_id += 1
        cur_indices = []
        cur_texts = []
        cur_sents = 0
        cur_tokens = 0

    for i, seg in enumerate(segments):
        text = str(seg or "").strip()
        if not text:
            continue
        seg_tokens = estimate_tokens(text)
        seg_sents = count_sentences(text)

        would_tokens = cur_tokens + seg_tokens
        would_sents = cur_sents + seg_sents
        over_tokens = cur_indices and would_tokens > max_tokens
        over_sents = cur_indices and would_sents > max_sentences
        at_min = cur_tokens >= min_tokens or cur_sents >= min_sentences

        if cur_indices and (over_tokens or over_sents) and at_min:
            _flush()

        cur_indices.append(i)
        cur_texts.append(text)
        cur_tokens += seg_tokens
        cur_sents += seg_sents

        if cur_tokens >= max_tokens or cur_sents >= max_sentences:
            _flush()

    _flush()
    return batches


def split_batch_translation(
    batch: TranslationBatch,
    translated_combined: str,
    *,
    fallback_per_segment: list[str] | None = None,
) -> dict[int, str]:
    """Map batch translation back to per-segment strings."""
    out: dict[int, str] = {}
    combined = str(translated_combined or "").strip()
    if not combined:
        for idx, src in zip(batch.segment_indices, batch.source_texts):
            out[idx] = ""
        return out

    parts = [p.strip() for p in combined.split("\n\n") if p.strip()]
    if len(parts) == len(batch.segment_indices):
        for idx, part in zip(batch.segment_indices, parts):
            out[idx] = part
        return out

    # Proportional split by source char length when delimiter merge failed.
    src_lens = [max(1, len(t)) for t in batch.source_texts]
    total = sum(src_lens)
    words = combined.split()
    if not words:
        for idx in batch.segment_indices:
            out[idx] = ""
        return out

    pos = 0
    for idx, slen in zip(batch.segment_indices, src_lens):
        n_words = max(1, round(len(words) * slen / total))
        chunk = " ".join(words[pos : pos + n_words]).strip()
        pos += n_words
        out[idx] = chunk

    if fallback_per_segment:
        for j, idx in enumerate(batch.segment_indices):
            if not out.get(idx) and j < len(fallback_per_segment):
                out[idx] = fallback_per_segment[j]

    return out
