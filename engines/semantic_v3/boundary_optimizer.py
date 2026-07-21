"""P32 — Sentence Boundary Optimizer (true sentence boundaries)."""

from __future__ import annotations

import re
from typing import Iterable

from engines.semantic_v3.sentence_builder import (
    assert_sentences_atomic,
    build_sentences_from_words,
)
from engines.semantic_v3.types import SemanticSentence, SemanticWord

# Extended abbreviation list (EN/UK/RU)
_ABBREV = re.compile(
    r"(?i)\b("
    r"mr|mrs|ms|dr|prof|jr|sr|vs|etc|e\.g|i\.e|u\.s|u\.k|"
    r"т\.д|т\.п|ім|англ|укр|рис|стор|проф|гр|"
    r"г|ул|пр|обл"
    r")\.?$"
)
_DIALOGUE_DASH = re.compile(r"^[\-—–]\s+")
_QUOTE_OPEN = re.compile(r'^["«“„]')
_ENUM_MARK = re.compile(r"^\d+[.)]\s+")


def optimize_boundaries(words: Iterable[SemanticWord]) -> list[SemanticSentence]:
    """
    P32/P104 — rebuild true sentence boundaries; fix Whisper fragmentation.
    Output is SemanticSentence only — Whisper segments must not exist as owners.
    """
    wlist = list(words)
    # Zeroth pass: split word stream on logical pauses / question starts
    wlist = _inject_soft_boundaries(wlist)
    # First pass: base builder
    sentences = build_sentences_from_words(wlist)
    # Second pass: split overlong complex sentences on ; or : with pause
    refined: list[SemanticSentence] = []
    for s in sentences:
        refined.extend(_maybe_split_complex(s))
    # Third pass: join false splits after abbreviations / initials
    refined = _join_false_splits(refined)
    # Fourth pass: join vocative fragments ("Hello" + "John") and repair casing
    refined = _join_vocative_and_greeting(refined)
    refined = _repair_punctuation(refined)
    assert_sentences_atomic(refined)
    return refined


def _inject_soft_boundaries(words: list[SemanticWord]) -> list[SemanticWord]:
    """Mark soft ends so Sentence Builder can split Hello John / How are you."""
    q_start = {
        "how", "what", "why", "when", "where", "who", "is", "are", "do", "does",
        "did", "can", "could", "як", "що", "чому", "коли", "де", "хто", "чи",
    }
    out: list[SemanticWord] = []
    for i, w in enumerate(words):
        out.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None:
            continue
        # Long pause before new capitalized question word → force end punct on current
        gap = max(w.pause_after_ms, nxt.pause_before_ms, nxt.start_ms - w.end_ms)
        nxt_bare = nxt.text.strip(".,!?;:\"'«»").lower()
        if gap >= 350 and nxt_bare in q_start and nxt.text[:1].isupper():
            if not w.text.endswith((".", "!", "?", ",")):
                w.text = w.text.rstrip() + "."
                w.pause_after_ms = max(w.pause_after_ms, gap)
    return out


def _join_vocative_and_greeting(
    sentences: list[SemanticSentence],
) -> list[SemanticSentence]:
    """P104 — Hello / John → Hello John, ; keep How are you? separate."""
    if len(sentences) < 2:
        return sentences
    greetings = {
        "hello", "hi", "hey", "привет", "вітаю", "здрастуй", "добрий",
    }
    out: list[SemanticSentence] = []
    i = 0
    while i < len(sentences):
        cur = sentences[i]
        words = [w.text.strip(".,!?;:") for w in cur.words]
        if (
            i + 1 < len(sentences)
            and len(words) <= 2
            and words
            and words[0].lower() in greetings
            and len(sentences[i + 1].words) == 1
            and sentences[i + 1].words[0].text[:1].isupper()
            and (sentences[i + 1].start_ms - cur.end_ms) < 600
        ):
            nxt = sentences[i + 1]
            merged_words = list(cur.words) + list(nxt.words)
            # Add comma after greeting name if next sentence continues
            name = merged_words[-1]
            if not name.text.endswith((",", "!", "?")):
                name.text = name.text.rstrip(".!") + ","
            text = " ".join(w.text for w in merged_words)
            text = re.sub(r"\s+([.,!?;:…])", r"\1", text)
            out.append(
                SemanticSentence(
                    text=text,
                    words=merged_words,
                    start_ms=merged_words[0].start_ms,
                    end_ms=merged_words[-1].end_ms,
                    speaker=cur.speaker,
                    emotion=cur.emotion,
                    has_address=True,
                    is_dialogue=True,
                )
            )
            i += 2
            continue
        # Merge single capitalized fragment into previous short greeting
        if (
            out
            and len(cur.words) == 1
            and cur.words[0].text[:1].isupper()
            and len(out[-1].words) <= 2
            and (out[-1].words[0].text.strip(".,!?").lower() in greetings)
            and (cur.start_ms - out[-1].end_ms) < 600
        ):
            prev = out.pop()
            merged_words = list(prev.words) + list(cur.words)
            if not merged_words[-1].text.endswith((",", "!", "?")):
                merged_words[-1].text = merged_words[-1].text.rstrip(".") + ","
            text = " ".join(w.text for w in merged_words)
            text = re.sub(r"\s+([.,!?;:…])", r"\1", text)
            out.append(
                SemanticSentence(
                    text=text,
                    words=merged_words,
                    start_ms=merged_words[0].start_ms,
                    end_ms=merged_words[-1].end_ms,
                    speaker=prev.speaker,
                    has_address=True,
                    is_dialogue=True,
                )
            )
            i += 1
            continue
        out.append(cur)
        i += 1
    return out


def _repair_punctuation(sentences: list[SemanticSentence]) -> list[SemanticSentence]:
    """Ensure questions/exclamations get terminal punctuation when intent clear."""
    q_start = re.compile(
        r"(?i)^(how|what|why|when|where|who|is|are|do|does|did|can|could|"
        r"як|що|чому|коли|де|хто|чи)\b"
    )
    for s in sentences:
        t = (s.text or "").rstrip()
        if not t:
            continue
        if q_start.match(t) and not t.endswith("?"):
            s.text = t + "?"
            if s.words and not s.words[-1].text.endswith("?"):
                s.words[-1].text = s.words[-1].text.rstrip(".!") + "?"
            s.is_question = True
        elif s.is_exclamation and not t.endswith("!"):
            s.text = t + "!"
    return sentences


def _maybe_split_complex(sent: SemanticSentence) -> list[SemanticSentence]:
    if len(sent.words) < 18:
        return [sent]
    # Split on semicolon / colon word if pause after ≥ 180ms
    cut_at: list[int] = []
    for i, w in enumerate(sent.words[:-1]):
        if w.text.endswith((";", ":")) and w.pause_after_ms >= 180:
            cut_at.append(i)
    if not cut_at:
        return [sent]
    parts: list[SemanticSentence] = []
    start = 0
    for idx in cut_at + [len(sent.words) - 1]:
        chunk = sent.words[start : idx + 1]
        if not chunk:
            continue
        text = " ".join(x.text for x in chunk)
        text = re.sub(r"\s+([.,!?;:…])", r"\1", text)
        parts.append(
            SemanticSentence(
                text=text,
                words=list(chunk),
                start_ms=chunk[0].start_ms,
                end_ms=chunk[-1].end_ms,
                speaker=sent.speaker,
                emotion=sent.emotion,
                is_complex=True,
            )
        )
        start = idx + 1
    return parts or [sent]


def _join_false_splits(sentences: list[SemanticSentence]) -> list[SemanticSentence]:
    if len(sentences) < 2:
        return sentences
    out: list[SemanticSentence] = []
    i = 0
    while i < len(sentences):
        cur = sentences[i]
        if i + 1 < len(sentences):
            last = cur.words[-1].text if cur.words else ""
            bare = re.sub(r"[.!?…]+$", "", last).strip()
            if _ABBREV.search(bare) or (len(bare) <= 2 and bare.isupper()):
                nxt = sentences[i + 1]
                merged_words = list(cur.words) + list(nxt.words)
                text = " ".join(w.text for w in merged_words)
                text = re.sub(r"\s+([.,!?;:…])", r"\1", text)
                out.append(
                    SemanticSentence(
                        text=text,
                        words=merged_words,
                        start_ms=merged_words[0].start_ms,
                        end_ms=merged_words[-1].end_ms,
                        speaker=cur.speaker,
                        emotion=cur.emotion,
                    )
                )
                i += 2
                continue
        # Tag dialogue / enum
        if _DIALOGUE_DASH.match(cur.text) or _QUOTE_OPEN.match(cur.text):
            cur.is_dialogue = True
            cur.is_direct_speech = True
        if _ENUM_MARK.match(cur.text):
            cur.is_enumeration = True
        out.append(cur)
        i += 1
    return out
