"""P3 Semantic Graph + P4 Context Engine."""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from engines.semantic_v3.types import SemanticSentence

_VERB_EN = re.compile(
    r"\b(is|are|was|were|be|been|have|has|had|do|does|did|go|goes|went|"
    r"make|made|say|said|get|got|know|knew|think|thought|take|took|"
    r"come|came|see|saw|want|need|drive|drove|hear|heard)\b",
    re.I,
)
_VERB_UK = re.compile(
    r"\b(є|був|була|було|бути|має|мав|робити|зробив|сказати|сказав|"
    r"йти|пішов|знати|знав|думати|взяти|прийти|бачити|хотіти|їхати|почув)\b",
    re.I,
)
_PRONOUN = re.compile(
    r"\b(he|she|it|they|him|her|them|his|their|this|that|"
    r"він|вона|воно|вони|його|її|їх|цей|ця|це)\b",
    re.I,
)


def analyze_sentence(sent: SemanticSentence) -> SemanticSentence:
    """Fill Semantic Graph fields (P3/P107) — deterministic heuristics."""
    text = sent.text
    tokens = re.findall(r"[\w'’-]+", text, re.UNICODE)
    entities = []
    for w in sent.words:
        if w.entity or w.entity_type:
            entities.append(w.text.strip(".,!?"))
        elif w.text[:1].isupper() and len(w.text) > 1 and w.text.lower() not in {
            "i", "a", "the", "an"
        }:
            entities.append(w.text.strip(".,!?"))
    seen: set[str] = set()
    sent.entities = []
    for e in entities:
        key = e.lower()
        if key not in seen:
            seen.add(key)
            sent.entities.append(e)

    verbs = []
    for t in tokens:
        if _VERB_EN.match(t) or _VERB_UK.match(t):
            verbs.append(t.lower())
    sent.verbs = verbs[:8]
    sent.subjects = sent.entities[:2]
    sent.objects = sent.entities[2:5]
    sent.intent = (
        "question"
        if text.rstrip().endswith("?")
        else ("exclaim" if text.rstrip().endswith("!") else "statement")
    )
    h = hashlib.sha256(text.lower().encode("utf-8")).digest()
    sent.meaning_vector = [b / 255.0 for b in h[:16]]

    relations: list[str] = [f"entity:{e}" for e in sent.entities[:5]]
    # P107 richer graph tags
    if sent.subjects and sent.verbs:
        relations.append(f"action:{sent.subjects[0]}->{sent.verbs[0]}")
    if sent.verbs and sent.objects:
        relations.append(f"object:{sent.verbs[0]}->{sent.objects[0]}")
    low = text.lower()
    if re.search(r"\b(because|тому що|бо)\b", low):
        relations.append("cause")
    if re.search(r"\b(so|therefore|тому|отже)\b", low):
        relations.append("effect")
    if re.search(r"\b(if|якщо|коли)\b", low):
        relations.append("condition")
    if re.search(r"\b(not|never|не|ні)\b", low):
        relations.append("negation")
    sent.relations = list(dict.fromkeys(relations))
    return sent


def analyze_all(sentences: Iterable[SemanticSentence]) -> list[SemanticSentence]:
    out = [analyze_sentence(s) for s in sentences]
    return attach_context(out)


def attach_context(sentences: list[SemanticSentence]) -> list[SemanticSentence]:
    """P4 — each sentence knows neighbors; flag pronoun risk without antecedent."""
    for i, s in enumerate(sentences):
        links: list[str] = []
        if i > 0:
            links.append(sentences[i - 1].sentence_uuid)
        if i + 1 < len(sentences):
            links.append(sentences[i + 1].sentence_uuid)
        s.context_links = links
        if _PRONOUN.search(s.text) and i > 0 and not sentences[i - 1].entities:
            s.relations = list(s.relations) + ["pronoun_without_clear_antecedent"]
    return sentences
