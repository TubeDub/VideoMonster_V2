"""P1 — Word Timestamp Engine."""

from __future__ import annotations

import re
from typing import Any

from engines.semantic_v3.types import SemanticWord

_WORD_RE = re.compile(r"\S+", re.UNICODE)
_ENTITY_CAP = re.compile(r"^[A-ZА-ЯЁІЇЄҐ][\w'’-]+$")
_NUMBER = re.compile(r"^[\d.,]+%?$")


def _guess_entity(text: str) -> str:
    t = text.strip(".,!?;:\"'«»")
    if _NUMBER.match(t):
        return "NUMBER"
    if _ENTITY_CAP.match(t) and len(t) > 1:
        return "PERSON"  # heuristic; refined later by NER
    return ""


def _syllables(text: str) -> int:
    return max(1, sum(1 for c in text.lower() if c in "aeiouyаеєиіїоуюяё"))


def from_word_token(
    text: str,
    start_ms: int,
    end_ms: int,
    *,
    confidence: float = 1.0,
    speaker: str = "",
    pause_before_ms: int = 0,
    pause_after_ms: int = 0,
) -> SemanticWord:
    clean = str(text or "").strip()
    return SemanticWord(
        text=clean,
        start_ms=int(start_ms),
        end_ms=max(int(start_ms) + 20, int(end_ms)),
        confidence=float(confidence),
        speaker=speaker,
        syllables=_syllables(clean),
        entity=_guess_entity(clean),
        importance=0.8 if _guess_entity(clean) else 0.5,
        pause_before_ms=max(0, int(pause_before_ms)),
        pause_after_ms=max(0, int(pause_after_ms)),
        breath_before=pause_before_ms >= 350,
        breath_after=pause_after_ms >= 350,
    )


def build_words_from_timing_map(
    source_segments: list[str],
    timing_map: list[Any],
    word_maps: list[Any] | None = None,
) -> list[SemanticWord]:
    """Flatten ASR into a continuous SemanticWord lattice. Whisper segs are not owners."""
    words: list[SemanticWord] = []
    maps = list(word_maps or [])

    for i, text in enumerate(source_segments):
        start = end = 0
        if i < len(timing_map):
            row = timing_map[i]
            if isinstance(row, dict):
                start = int(row.get("start") or row.get("start_ms") or 0)
                end = int(row.get("end") or row.get("end_ms") or start)
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                start, end = int(row[0]), int(row[1])

        seg_words: list[SemanticWord] = []
        # Prefer real word maps
        if i < len(maps):
            wm = maps[i]
            raw_words = []
            if isinstance(wm, dict):
                raw_words = wm.get("words") or []
            elif hasattr(wm, "words"):
                raw_words = getattr(wm, "words") or []
            for j, w in enumerate(raw_words):
                if isinstance(w, dict):
                    seg_words.append(
                        from_word_token(
                            w.get("text") or w.get("word") or "",
                            int(w.get("start_ms") or 0),
                            int(w.get("end_ms") or 0),
                            confidence=float(w.get("confidence") or 1.0),
                        )
                    )
                else:
                    seg_words.append(
                        from_word_token(
                            getattr(w, "text", ""),
                            int(getattr(w, "start_ms", 0)),
                            int(getattr(w, "end_ms", 0)),
                            confidence=float(getattr(w, "confidence", 1.0)),
                        )
                    )

        # Timing_map embedded words
        if not seg_words and i < len(timing_map) and isinstance(timing_map[i], dict):
            for w in timing_map[i].get("words") or []:
                if isinstance(w, dict):
                    st = w.get("start_ms")
                    if st is None and w.get("start") is not None:
                        st = int(round(float(w["start"]) * 1000))
                    en = w.get("end_ms")
                    if en is None and w.get("end") is not None:
                        en = int(round(float(w["end"]) * 1000))
                    seg_words.append(
                        from_word_token(
                            w.get("word") or w.get("text") or "",
                            int(st or start),
                            int(en or end),
                            confidence=float(w.get("probability") or w.get("confidence") or 1.0),
                        )
                    )

        # Proportional fallback from segment text
        if not seg_words:
            tokens = _WORD_RE.findall(str(text or "").strip())
            if tokens:
                span = max(1, end - start)
                weights = [max(1, len(t)) for t in tokens]
                total = sum(weights)
                cursor = start
                for j, tok in enumerate(tokens):
                    if j == len(tokens) - 1:
                        w_end = end
                    else:
                        share = int(span * weights[j] / total)
                        w_end = min(end, cursor + max(40, share))
                    seg_words.append(
                        from_word_token(tok, cursor, w_end, confidence=0.5)
                    )
                    cursor = w_end

        # Pause annotations between words
        for j in range(len(seg_words) - 1):
            gap = seg_words[j + 1].start_ms - seg_words[j].end_ms
            if gap > 0:
                seg_words[j].pause_after_ms = gap
                seg_words[j + 1].pause_before_ms = gap
                if gap >= 350:
                    seg_words[j].breath_after = True
                    seg_words[j + 1].breath_before = True

        words.extend(seg_words)

    return words
