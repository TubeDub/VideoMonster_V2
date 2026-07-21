"""P2 — Sentence Builder (from words, not Whisper segments)."""

from __future__ import annotations

import re
from typing import Iterable

from engines.semantic_v3.types import SemanticSentence, SemanticWord

_END_PUNCT = re.compile(r"[.!?…]+[\"'»”]?$")
_ABBREV = re.compile(
    r"(?i)\b(mr|mrs|ms|dr|prof|jr|sr|vs|etc|e\.g|i\.e|т\.д|т\.п|ім|англ)\.?$"
)
_ENUM = re.compile(r"(?:,|;|\band\b|\bor\b|\bта\b|\bі\b)", re.I)
_DIRECT = re.compile(r'^[\"«“]|[\"»”]$')
_SUBORD = re.compile(
    r"(?i)\b(because|that|which|who|when|while|if|although|though|"
    r"тому що|який|яка|яке|коли|якщо|хоча)\b"
)


def _is_sentence_end(word: SemanticWord, next_word: SemanticWord | None) -> bool:
    t = word.text.strip()
    if not _END_PUNCT.search(t):
        return False
    # Don't split on abbreviations / initials
    bare = _END_PUNCT.sub("", t).strip()
    if _ABBREV.search(bare):
        return False
    if len(bare) <= 2 and bare.isupper():
        return False
    # Long pause after end punct → hard boundary
    if next_word is None:
        return True
    if word.pause_after_ms >= 200 or next_word.pause_before_ms >= 200:
        return True
    # Capitalized next word after end punct
    nxt = next_word.text.lstrip("\"'««")
    if nxt and nxt[0].isupper():
        return True
    return True


def _tag_sentence(sent: SemanticSentence) -> None:
    text = sent.text
    sent.is_direct_speech = bool(_DIRECT.search(text))
    sent.is_enumeration = len(_ENUM.findall(text)) >= 3
    sent.is_subordinate = bool(_SUBORD.search(text))
    sent.is_complex = text.count(",") >= 2 or sent.is_subordinate
    sent.has_parenthetical = "(" in text or "—" in text or " – " in text
    # Dialogue heuristic: short + address-like
    sent.is_dialogue = len(text.split()) <= 12 and (
        text.endswith("?") or text.startswith(("—", "-", "–"))
    )
    sent.has_address = bool(re.search(r"\b(sir|madam|пан|пані|друзі)\b", text, re.I))


def build_sentences_from_words(
    words: Iterable[SemanticWord],
) -> list[SemanticSentence]:
    """P0 Absolute: each sentence is atomic — never split across dub segments later."""
    wlist = [w for w in words if str(w.text or "").strip()]
    if not wlist:
        return []

    sentences: list[SemanticSentence] = []
    buf: list[SemanticWord] = []

    for i, w in enumerate(wlist):
        buf.append(w)
        nxt = wlist[i + 1] if i + 1 < len(wlist) else None
        if _is_sentence_end(w, nxt) or nxt is None:
            text = " ".join(x.text for x in buf).strip()
            text = re.sub(r"\s+([.,!?;:…])", r"\1", text)
            sent = SemanticSentence(
                text=text,
                words=list(buf),
                start_ms=buf[0].start_ms,
                end_ms=buf[-1].end_ms,
                speaker=buf[0].speaker,
            )
            _tag_sentence(sent)
            sentences.append(sent)
            buf = []

    # Link neighbors (P4 prep)
    for i, s in enumerate(sentences):
        links = []
        if i > 0:
            links.append(sentences[i - 1].sentence_uuid)
        if i + 1 < len(sentences):
            links.append(sentences[i + 1].sentence_uuid)
        s.context_links = links

    return sentences


def assert_sentences_atomic(sentences: list[SemanticSentence]) -> None:
    """P20 — each sentence must have contiguous word span; no empty shells."""
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    for s in sentences:
        if not s.text.strip():
            raise ArchitectureViolation(
                "P20: empty sentence",
                stage="sentence_builder",
                rule="sentence_atomic",
                segment_id=s.sentence_uuid,
            )
        if s.words and s.words[0].start_ms > s.words[-1].end_ms:
            raise ArchitectureViolation(
                "P20: inverted word timing in sentence",
                stage="sentence_builder",
                rule="sentence_atomic",
                segment_id=s.sentence_uuid,
            )
