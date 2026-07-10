"""
Internal A/B comparison of translation strategies.
Used by scripts/test_translation_universal.py — not in production hot path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class CompareCandidate:
    name: str
    segments: list[str]
    score: float
    details: dict[str, Any]


def _word_tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w']+", (text or "").lower(), flags=re.UNICODE))


def score_translation_quality(
    source_segments: list[str],
    translated_segments: list[str],
    *,
    src_lang: str,
    tgt_lang: str,
) -> tuple[float, dict[str, Any]]:
    """
    Heuristic quality score (0..100) — language-agnostic.
    Higher = more likely natural / meaningful dubbing text.
    """
    if not translated_segments:
        return 0.0, {"reason": "empty"}

    src_lang = (src_lang or "en").lower().split("-")[0]
    tgt_lang = (tgt_lang or "ru").lower().split("-")[0]

    n = max(len(source_segments), len(translated_segments))
    empty = 0
    identical = 0
    too_short = 0
    dup_bigrams = 0
    overlap_with_src = 0.0
    lengths_ok = 0

    for i in range(n):
        src = str(source_segments[i] if i < len(source_segments) else "").strip()
        tgt = str(translated_segments[i] if i < len(translated_segments) else "").strip()
        if not tgt:
            empty += 1
            continue
        if src and src.lower() == tgt.lower() and src_lang != tgt_lang:
            identical += 1
        if src and len(tgt) < max(3, len(src) * 0.15):
            too_short += 1
        words = tgt.lower().split()
        if len(words) >= 2:
            bigrams = [f"{words[j]} {words[j+1]}" for j in range(len(words) - 1)]
            if len(bigrams) != len(set(bigrams)):
                dup_bigrams += 1
        ws = _word_tokens(src)
        wt = _word_tokens(tgt)
        if ws and wt:
            overlap_with_src += len(ws & wt) / max(len(ws), 1)
        ratio = len(tgt) / max(len(src), 1)
        if 0.25 <= ratio <= 3.5:
            lengths_ok += 1

    filled = max(1, n - empty)
    overlap_avg = overlap_with_src / filled
    length_ratio_score = lengths_ok / filled

    penalty = (
        empty * 8
        + identical * 12
        + too_short * 6
        + dup_bigrams * 4
        + overlap_avg * 15
    )
    score = max(0.0, min(100.0, 70.0 + length_ratio_score * 25.0 - penalty))

    return score, {
        "empty": empty,
        "identical_to_source": identical,
        "too_short": too_short,
        "dup_bigram_segments": dup_bigrams,
        "avg_src_word_overlap": round(overlap_avg, 3),
        "length_ratio_ok": lengths_ok,
    }


def compare_strategies(
    source_segments: list[str],
    timing_map: list,
    src_lang: str,
    tgt_lang: str,
    strategies: dict[str, Callable[[], list[str]]],
) -> list[CompareCandidate]:
    """Run multiple translation functions on same input; return ranked candidates."""
    results: list[CompareCandidate] = []
    for name, fn in strategies.items():
        try:
            segs = fn()
        except Exception as e:
            results.append(
                CompareCandidate(name=name, segments=[], score=0.0, details={"error": str(e)})
            )
            continue
        score, details = score_translation_quality(
            source_segments, segs, src_lang=src_lang, tgt_lang=tgt_lang
        )
        results.append(
            CompareCandidate(name=name, segments=segs, score=score, details=details)
        )
    results.sort(key=lambda c: c.score, reverse=True)
    return results


def pick_best_strategy(
    source_segments: list[str],
    timing_map: list,
    src_lang: str,
    tgt_lang: str,
    strategies: dict[str, Callable[[], list[str]]],
) -> CompareCandidate:
    ranked = compare_strategies(
        source_segments, timing_map, src_lang, tgt_lang, strategies
    )
    return ranked[0] if ranked else CompareCandidate("none", [], 0.0, {})
