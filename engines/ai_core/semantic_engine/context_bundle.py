"""Dialogue context bundle for context-aware semantic adaptation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DialogueContext:
    """Surrounding dialogue context — never translate in isolation."""

    index: int
    source: str
    translated: str
    prev_sources: list[str] = field(default_factory=list)
    next_sources: list[str] = field(default_factory=list)
    prev_semantic: list[str] = field(default_factory=list)
    next_translated: list[str] = field(default_factory=list)
    speaker: str = ""
    emotion: str = ""
    topic_hint: str = ""
    situation: str = ""

    def prev_context_text(self) -> str:
        parts = self.prev_semantic or self.prev_sources
        return " ".join(p.strip() for p in parts if p.strip())[-400:]

    def next_context_text(self) -> str:
        return " ".join(p.strip() for p in self.next_translated if p.strip())[:400]

    def prompt_block(self) -> str:
        lines = []
        if self.prev_sources:
            lines.append(f"Previous: {' | '.join(self.prev_sources[-2:])}")
        if self.next_sources:
            lines.append(f"Next: {' | '.join(self.next_sources[:2])}")
        if self.speaker:
            lines.append(f"Speaker: {self.speaker}")
        if self.emotion:
            lines.append(f"Emotion: {self.emotion}")
        if self.topic_hint:
            lines.append(f"Topic: {self.topic_hint}")
        if self.situation:
            lines.append(f"Situation: {self.situation}")
        return "\n".join(lines)


_TOPIC_KEYWORDS = {
    "en": (
        (re.compile(r"\bcar|race|driving|road\b", re.I), "automotive / driving"),
        (re.compile(r"\bfilm|cinema|movie|camera|USC\b", re.I), "filmmaking / cinema"),
        (re.compile(r"\bhospital|injur|crash|accident\b", re.I), "accident / recovery"),
        (re.compile(r"\buniversity|school|apply\b", re.I), "education / application"),
    ),
}


def _infer_topic(sources: list[str]) -> str:
    blob = " ".join(sources)
    for _lang, rules in _TOPIC_KEYWORDS.items():
        for pattern, label in rules:
            if pattern.search(blob):
                return label
    return "general narrative"


def _infer_situation(source: str, *, prev: list[str]) -> str:
    s = source.lower()
    if re.search(r"\bcrash|accident|black|ejected|hospital\b", s):
        return "dramatic / accident aftermath"
    if re.search(r"\bdinner|home|driving\b", s):
        return "everyday / personal"
    if re.search(r"\brace|finish line|camera\b", s):
        return "event / public"
    if prev and re.search(r"\bbut|however|although\b", source, re.I):
        return "contrast / continuation"
    return "dialogue continuation"


def build_dialogue_context(
    segments: list[dict[str, Any]],
    index: int,
    manifest: dict[str, Any] | None = None,
) -> DialogueContext:
    """Build prev/next context window for segment index."""
    manifest = manifest or {}
    seg = segments[index] if 0 <= index < len(segments) else {}
    source = str(seg.get("text") or "").strip()
    translated = str(seg.get("translated_text") or "").strip()

    prev_sources: list[str] = []
    prev_sem: list[str] = []
    for j in range(max(0, index - 2), index):
        s = segments[j]
        if s.get("merged_into") is not None:
            continue
        prev_sources.append(str(s.get("text") or "").strip())
        prev_sem.append(
            str(s.get("semantic_text") or s.get("translated_text") or "").strip()
        )

    next_sources: list[str] = []
    next_trans: list[str] = []
    for j in range(index + 1, min(len(segments), index + 3)):
        s = segments[j]
        if s.get("merged_into") is not None:
            continue
        next_sources.append(str(s.get("text") or "").strip())
        next_trans.append(str(s.get("translated_text") or "").strip())

    all_sources = prev_sources + [source] + next_sources
    topic = _infer_topic(all_sources)
    if manifest.get("content_type"):
        topic = f"{manifest.get('content_type')} / {topic}"

    emotion = str(
        seg.get("emotion")
        or (seg.get("tts_emotion") or {}).get("emotion")
        or (seg.get("creative_brief") or {}).get("dominant_emotion")
        or "neutral"
    )
    speaker = str(seg.get("speaker") or "narrator")
    situation = _infer_situation(source, prev=prev_sources)

    return DialogueContext(
        index=index,
        source=source,
        translated=translated,
        prev_sources=prev_sources,
        next_sources=next_sources,
        prev_semantic=prev_sem,
        next_translated=next_trans,
        speaker=speaker,
        emotion=emotion,
        topic_hint=topic,
        situation=situation,
    )
