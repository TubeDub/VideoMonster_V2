"""P112 — Emotion Engine (pre-Translation)."""

from __future__ import annotations

import re

from engines.semantic_v3.types import SemanticSentence

# Deterministic keyword maps (EN/UK)
_EMOTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("joy", re.compile(r"(?i)\b(happy|joy|glad|great|wonderful|love|сміх|радість|щаслив)\b")),
    ("fear", re.compile(r"(?i)\b(afraid|scared|fear|terror|страх|боюсь|жах)\b")),
    ("anger", re.compile(r"(?i)\b(angry|mad|hate|damn|furious|злий|гнів|чорт)\b")),
    ("irony", re.compile(r"(?i)\b(yeah right|sure thing|obviously|ну звісно|аякже)\b")),
    ("sarcasm", re.compile(r"(?i)\b(oh great|just perfect|wonderful\.\.\.|о чудово)\b")),
    ("sadness", re.compile(r"(?i)\b(sad|cry|tears|sorry|miss|сумн|плач|жаль)\b")),
    ("surprise", re.compile(r"(?i)\b(wow|what\?!|really\?|дивн|невероят|серйозно)\b")),
    ("calm", re.compile(r"(?i)\b(calm|okay|fine|alright|спокійн|добре|гаразд)\b")),
]


def detect_emotion(sent: SemanticSentence) -> str:
    text = sent.text or ""
    if text.endswith("!"):
        # Prefer anger/surprise for bang without softer cues
        if _EMOTION_PATTERNS[2][1].search(text):
            sent.emotion = "anger"
            return sent.emotion
        if "?" in text:
            sent.emotion = "surprise"
            return sent.emotion
    for name, pat in _EMOTION_PATTERNS:
        if pat.search(text):
            sent.emotion = name
            for w in sent.words:
                if not w.emotion_hint:
                    w.emotion_hint = name
            return name
    if sent.emotion and sent.emotion != "neutral":
        return sent.emotion
    sent.emotion = "calm" if text.endswith(".") else "neutral"
    return sent.emotion


def apply_emotion_engine(sentences: list[SemanticSentence]) -> list[SemanticSentence]:
    for s in sentences:
        detect_emotion(s)
    return sentences
